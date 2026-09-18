#!/usr/bin/env python
"""API test server — a small FastAPI stand-in for "the other system" on
the receiving end of the pre/post call-type hooks (see API.md and
app/services/admin/call_hooks.py), AND a minimal browser-based test
harness for the live-recording WebSocket API (streaming, transcript,
speaker names, live coach score, suggestions, blockers — everything a
real integrator would need to reproduce Corella's own live UI using
nothing but the documented API). Committed on purpose, not a scratch
file: `tests/test_call_hooks_integration.py` runs this exact app as a
real subprocess and asserts against real HTTP responses, and it's just
as usable by hand against the real running dev stack.

Run it:

    cd server && .venv/bin/uvicorn scripts.api_test_server:app --port 9199 --reload

--- Testing pre/post call-type hooks ---

Point a call type's pre_call_url/post_call_url at it from inside the
api/worker containers via `host.docker.internal`, since it runs on your
host machine, not on the corella_default docker network:

    pre_call_url  = http://host.docker.internal:9199/pre
    post_call_url = http://host.docker.internal:9199/post

Every request is logged to the console (method, path, the three
mandatory headers pulled out specifically, and the body) and kept
in-memory — GET /requests to inspect everything received so far as JSON,
or DELETE /requests to clear it between test runs.

Behavior:
  - Any path containing "pre"  -> 200, returns a fake CRM-lookup-shaped
    text body, for testing pre_call_use_as_context.
  - Any path containing "fail" -> 500, for testing that a broken pre/post
    hook is swallowed gracefully rather than breaking meeting
    creation/report generation.
  - Any path containing "slow" -> sleeps 8s before responding (longer
    than the default pre_call_timeout_seconds=5s), for testing the
    timeout path.
  - Everything else (including "post") -> 200, {"ok": true} — logs
    whatever body was sent (the full structured payload, if
    post_call_send_full_payload is on) so you can eyeball it.

--- Testing the live-recording WebSocket API ---

Open http://localhost:9199/ in a browser (needs mic permission). Paste
in an API key (Settings -> API keys on the real app) and the real API's
base URL (e.g. http://localhost:8090), hit Start: it creates a meeting
via the real REST API, opens the real live WebSocket authenticated with
that API key (no browser login involved), streams your mic the exact
same way the real app's own capture code does (same PCM16/16kHz
resampling, via the same worklet, served here at /pcm-worklet.js), and
renders every message type the protocol sends back in real time —
transcript (with speaker labels, updated live as diarization resolves
them), live partial-transcript previews, and the live copilot's coach
score/suggestion/blockers/action items. Hit Stop to finalize the meeting
exactly like ending a real recording would (auto-report, post-call hook
if configured).

The meeting-creation POST goes through this server (/start-test-call)
rather than straight from the browser, deliberately — a cross-origin
fetch() with an Authorization header triggers a CORS preflight the real
app's CORS_ORIGINS won't have been configured to allow for this test
tool's own origin, and there's no reason to make the user edit their
real instance's config just to run this. The WebSocket connection itself
*is* opened directly browser-to-API — cross-origin WS isn't subject to
that same restriction, so no proxying is needed (or possible, for a
binary audio stream) there.
"""

import asyncio
import json
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

app = FastAPI(title="Corella API test server")

_MANDATORY_HEADERS = ["x-corella-app-url", "x-corella-meeting-id", "x-corella-user-id"]
_log: list[dict] = []


def _print_request(method: str, path: str, headers: dict, body: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"{datetime.now(UTC).isoformat()}  {method} {path}")
    print("-- mandatory headers --")
    for h in _MANDATORY_HEADERS:
        present = h in {k.lower() for k in headers}
        value = next((v for k, v in headers.items() if k.lower() == h), None)
        marker = "✓" if present else "✗ MISSING"
        print(f"  {h}: {value}  [{marker}]")
    other = {k: v for k, v in headers.items() if k.lower() not in _MANDATORY_HEADERS}
    if other:
        print("-- other headers --")
        for k, v in other.items():
            if k.lower() in ("host", "content-length", "accept", "accept-encoding", "user-agent", "connection"):
                continue
            print(f"  {k}: {v}")
    print("-- body --")
    try:
        print(json.dumps(json.loads(body), indent=2)[:4000])
    except (json.JSONDecodeError, TypeError):
        print(body[:2000] if body else "(empty)")
    print("=" * 70)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def catch_all(path: str, request: Request):
    # --- Test-harness machinery (not hook deliveries — never logged) ---
    if path == "":
        return HTMLResponse(_LIVE_TEST_PAGE_HTML)
    if path == "pcm-worklet.js":
        return Response(_PCM_WORKLET_JS, media_type="application/javascript")
    if path == "requests":
        if request.method == "DELETE":
            _log.clear()
            return JSONResponse({"cleared": True})
        return JSONResponse(_log)
    if path == "start-test-call" and request.method == "POST":
        return await _start_test_call(request)

    # --- Pre/post call-type hook delivery (logged) ---
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8", errors="replace")
    headers = dict(request.headers)

    entry = {
        "at": datetime.now(UTC).isoformat(),
        "method": request.method,
        "path": f"/{path}",
        "headers": headers,
        "body": body,
        "mandatory_headers_present": {
            h: any(k.lower() == h for k in headers) for h in _MANDATORY_HEADERS
        },
    }
    _log.append(entry)
    _print_request(request.method, f"/{path}", headers, body)

    if "fail" in path:
        return JSONResponse({"error": "simulated failure"}, status_code=500)
    if "slow" in path:
        await asyncio.sleep(8)
    if "pre" in path:
        return PlainTextResponse(
            "CRM lookup: Acme Corp is a Fortune 500 prospect, deal size $250k, "
            "champion is Jane Doe, last contacted 3 days ago about renewal terms."
        )
    return JSONResponse({"ok": True})


async def _start_test_call(request: Request) -> JSONResponse:
    """Server-side proxy for the one REST call the live-test page needs
    (see the module docstring for why: avoiding a CORS preflight against
    the real app's own CORS_ORIGINS). Everything else the page does
    (the WebSocket itself) talks directly browser-to-API.
    """
    payload = await request.json()
    api_base = payload["api_base"].rstrip("/")
    api_key = payload["api_key"]
    body = {"title": payload.get("title") or "API test server live check"}
    if payload.get("call_type_id"):
        body["call_type_id"] = payload["call_type_id"]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{api_base}/api/meetings",
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.RequestError as e:
        return JSONResponse({"error": f"Couldn't reach {api_base}: {e}"}, status_code=502)

    if response.status_code >= 400:
        return JSONResponse({"error": response.text}, status_code=response.status_code)
    return JSONResponse(response.json())


# Verbatim copy of web/public/pcm-worklet.js — the exact same downmix/
# resample-to-16kHz/PCM16 logic the real app's own mic capture uses, so
# this test harness streams audio in the identical format the live WS
# protocol expects (see API.md's WebSocket section for the wire format).
_PCM_WORKLET_JS = """
class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = options.processorOptions || {};
    this.targetRate = opts.targetSampleRate || 16000;
    this.chunkMs = opts.chunkMs || 200;
    this.ratio = sampleRate / this.targetRate;
    this.samplesPerChunk = Math.round((this.targetRate * this.chunkMs) / 1000);

    this.inputBuffer = new Float32Array(0);
    this.readPos = 0;
    this.outputBuffer = [];
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || input[0].length === 0) return true;

    const channelCount = input.length;
    const frameCount = input[0].length;
    const mono = new Float32Array(frameCount);
    for (let ch = 0; ch < channelCount; ch++) {
      const data = input[ch];
      for (let i = 0; i < frameCount; i++) mono[i] += data[i] / channelCount;
    }

    const combined = new Float32Array(this.inputBuffer.length + mono.length);
    combined.set(this.inputBuffer, 0);
    combined.set(mono, this.inputBuffer.length);
    this.inputBuffer = combined;

    while (this.readPos + this.ratio < this.inputBuffer.length - 1) {
      const idx = Math.floor(this.readPos);
      const frac = this.readPos - idx;
      const s0 = this.inputBuffer[idx];
      const s1 = this.inputBuffer[idx + 1];
      this.outputBuffer.push(s0 + (s1 - s0) * frac);
      this.readPos += this.ratio;
    }

    const consumed = Math.floor(this.readPos);
    if (consumed > 0) {
      this.inputBuffer = this.inputBuffer.slice(consumed);
      this.readPos -= consumed;
    }

    while (this.outputBuffer.length >= this.samplesPerChunk) {
      const chunk = this.outputBuffer.splice(0, this.samplesPerChunk);
      const pcm16 = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        const v = Math.max(-1, Math.min(1, chunk[i]));
        pcm16[i] = v < 0 ? v * 32768 : v * 32767;
      }
      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm-worklet", PCMWorkletProcessor);
"""

_LIVE_TEST_PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Corella API test server — live recording</title>
<style>
  body { font-family: -apple-system, sans-serif; margin: 0; background: #f7f7f5; color: #12141a; }
  header { padding: 14px 20px; background: #0b1b33; color: #fafaf9; }
  header h1 { margin: 0; font-size: 16px; }
  header p { margin: 4px 0 0; font-size: 12px; opacity: .8; }
  .layout { display: flex; gap: 16px; padding: 16px; align-items: flex-start; }
  .col { background: #fff; border: 1px solid #e4e4e1; border-radius: 8px; padding: 14px; }
  .config { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 16px; background: #fff; border-bottom: 1px solid #e4e4e1; align-items: center; }
  .config input { padding: 6px 8px; border: 1px solid #e4e4e1; border-radius: 6px; font-size: 12px; }
  .config input[type=text] { width: 220px; }
  button { padding: 7px 14px; border-radius: 6px; border: 1px solid #0b1b33; background: #0b1b33; color: #fafaf9; font-size: 12px; cursor: pointer; }
  button:disabled { opacity: .4; cursor: default; }
  button.secondary { background: #fff; color: #0b1b33; }
  #status { font-size: 12px; color: #5b5f6b; margin-left: 8px; }
  #transcriptCol { flex: 2; min-width: 380px; }
  #copilotCol { flex: 1; min-width: 260px; }
  #transcript { height: 360px; overflow-y: auto; font-size: 13px; line-height: 1.5; border: 1px solid #e4e4e1; border-radius: 6px; padding: 10px; background: #fafaf9; }
  #transcript .line b { color: #0b1b33; }
  #transcript .partial { opacity: .55; font-style: italic; }
  .label { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #8b8f99; margin: 14px 0 4px; }
  .label:first-child { margin-top: 0; }
  #score { font-size: 28px; font-weight: 600; }
  #suggestion { font-size: 13px; }
  ul { margin: 4px 0; padding-left: 18px; font-size: 13px; }
  li.danger { color: #b3261e; }
</style>
</head>
<body>
<header>
  <h1>Corella API test server — live recording</h1>
  <p>Exercises the documented WebSocket API exactly as an external integrator would build against it — no Corella frontend code involved.</p>
</header>

<div class="config">
  <label>API base <input type="text" id="apiBase" value="http://localhost:8090"></label>
  <label>API key <input type="text" id="apiKey" placeholder="sk_live_..." size="30"></label>
  <label>Call type ID <input type="text" id="callTypeId" placeholder="(optional — default type)"></label>
  <button id="startBtn" onclick="start()">Start</button>
  <button id="stopBtn" class="secondary" onclick="stop()" disabled>Stop</button>
  <span id="status">Idle.</span>
</div>

<div class="layout">
  <div class="col" id="transcriptCol">
    <div class="label">Transcript</div>
    <div id="transcript"></div>
  </div>
  <div class="col" id="copilotCol">
    <div class="label">Coach score</div>
    <div id="score">—</div>
    <div class="label">Suggestion</div>
    <div id="suggestion">Nothing yet.</div>
    <div class="label">Blockers</div>
    <ul id="blockers"><li>None.</li></ul>
    <div class="label">Action items</div>
    <ul id="actionItems"><li>None.</li></ul>
  </div>
</div>

<script>
let ws = null, meetingId = null, captureCleanup = null;
const segments = new Map();
// Labels live off the transcript row — `transcript` events have no
// speaker_label, and on the Deepgram path they often arrive *after*
// diarization_update for the same id (label is published to Redis
// before the transcript frame is sent). Storing the name on the same
// object and then segments.set(id, transcript) was wiping every split
// back to "me". Same split LiveSession.tsx uses.
const speakerLabels = new Map();
const partials = { me: "", them: "" };

for (const id of ["apiBase", "apiKey", "callTypeId"]) {
  const saved = localStorage.getItem("corella_test_" + id);
  if (saved) document.getElementById(id).value = saved;
  document.getElementById(id).addEventListener("change", (e) => localStorage.setItem("corella_test_" + id, e.target.value));
}

function setStatus(s) { document.getElementById("status").textContent = s; }
function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; }

function renderTranscript() {
  const sorted = [...segments.values()].sort((a, b) => a.start_ms - b.start_ms);
  let html = sorted.map((s) => {
    const label = speakerLabels.get(s.id) || s.speaker_label || s.channel;
    return `<div class="line"><b>${escapeHtml(label)}</b>: ${escapeHtml(s.text)}</div>`;
  }).join("");
  for (const ch of ["me", "them"]) {
    if (partials[ch]) html += `<div class="line partial"><b>${ch}</b>: ${escapeHtml(partials[ch])}…</div>`;
  }
  const el = document.getElementById("transcript");
  el.innerHTML = html || '<span style="color:#8b8f99">(waiting for speech…)</span>';
  el.scrollTop = el.scrollHeight;
}

function renderCopilot(msg) {
  document.getElementById("score").textContent = msg.coach_score ?? "—";
  document.getElementById("suggestion").textContent = msg.suggestion || "Nothing yet.";
  const blockers = msg.blockers || [];
  document.getElementById("blockers").innerHTML = blockers.length
    ? blockers.map((b) => `<li class="danger">${escapeHtml(b)}</li>`).join("")
    : "<li>None.</li>";
  const items = msg.action_items || [];
  document.getElementById("actionItems").innerHTML = items.length
    ? items.map((a) => `<li>${escapeHtml(a)}</li>`).join("")
    : "<li>None.</li>";
}

async function start() {
  const apiBase = document.getElementById("apiBase").value.replace(/\\/$/, "");
  const apiKey = document.getElementById("apiKey").value.trim();
  const callTypeId = document.getElementById("callTypeId").value.trim() || null;
  if (!apiKey) { setStatus("Paste an API key first (Settings -> API keys in the real app)."); return; }

  document.getElementById("startBtn").disabled = true;
  segments.clear(); speakerLabels.clear(); partials.me = ""; partials.them = "";
  renderTranscript(); renderCopilot({});
  setStatus("Creating meeting…");

  const createResp = await fetch("/start-test-call", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_base: apiBase, api_key: apiKey, call_type_id: callTypeId }),
  });
  const created = await createResp.json();
  if (!createResp.ok) {
    setStatus("Failed to create meeting: " + (created.error || createResp.status));
    document.getElementById("startBtn").disabled = false;
    return;
  }
  meetingId = created.id;
  setStatus(`Meeting ${meetingId} created — connecting…`);

  const wsUrl = apiBase.replace(/^http/, "ws") + "/ws/meetings/" + meetingId + "/live";
  ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", api_key: apiKey }));
  ws.onclose = () => setStatus("Disconnected.");
  ws.onerror = () => setStatus("WebSocket error — check the browser console.");
  ws.onmessage = onMessage;
  document.getElementById("stopBtn").disabled = false;
}

async function onMessage(event) {
  const msg = JSON.parse(event.data);
  switch (msg.type) {
    case "ready":
      setStatus("Recording — speak into your mic.");
      await beginCapture();
      break;
    case "copilot_unavailable":
      setStatus("Recording (no LLM connected for this account — no live suggestions).");
      break;
    case "transcript":
      segments.set(msg.segment.id, msg.segment);
      renderTranscript();
      break;
    case "partial_transcript":
      partials[msg.channel] = msg.text;
      renderTranscript();
      break;
    case "copilot":
      renderCopilot(msg);
      break;
    case "diarization_update":
    case "speaker_hint":
      for (const id of msg.removed_segment_ids || []) {
        segments.delete(id);
        speakerLabels.delete(id);
      }
      for (const seg of msg.segments || []) {
        segments.set(seg.id, seg);
        if (seg.speaker_label) speakerLabels.set(seg.id, seg.speaker_label);
      }
      renderTranscript();
      break;
    case "stopped":
      setStatus("Stopped — finalizing (report generates automatically).");
      break;
  }
}

async function beginCapture() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const audioCtx = new AudioContext();
  await audioCtx.audioWorklet.addModule("/pcm-worklet.js");
  const source = audioCtx.createMediaStreamSource(stream);
  const worklet = new AudioWorkletNode(audioCtx, "pcm-worklet", { processorOptions: { targetSampleRate: 16000, chunkMs: 200 } });
  worklet.port.onmessage = (e) => {
    const pcm = new Int16Array(e.data);
    const frame = new Uint8Array(1 + pcm.byteLength);
    frame[0] = 0; // channel 0 = "me" — this test harness only streams the mic, not a second "them" channel
    frame.set(new Uint8Array(pcm.buffer), 1);
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(frame);
  };
  source.connect(worklet);
  const silence = audioCtx.createGain();
  silence.gain.value = 0;
  worklet.connect(silence);
  silence.connect(audioCtx.destination);

  captureCleanup = () => {
    source.disconnect(); worklet.disconnect(); silence.disconnect();
    audioCtx.close(); stream.getTracks().forEach((t) => t.stop());
  };
}

function stop() {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
  if (captureCleanup) { captureCleanup(); captureCleanup = null; }
  document.getElementById("stopBtn").disabled = true;
  document.getElementById("startBtn").disabled = false;
  setStatus("Stopping…");
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9199)
