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

Every request is logged to the console (method, path, the four
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
base URL (e.g. http://localhost:8090), hit Start. Meeting create is
proxied here (`POST /start-test-call`) so you don't have to put this
origin in CORS_ORIGINS; the live socket and mic go through
`packages/corella-live` (served at `/corella-live/*`) — the same client
an integrator would npm-install. Build that package first:

    cd packages/corella-live && npm install && npm run build

The page then connects with `CorellaLive.connect`, streams the mic via
`startMic()`, and renders the package's already-labeled transcript plus
copilot events — including Corella's live score ring, sentiment fill, and
speaker-colored talk share (same colors and fill logic as the product UI).
Stop finalizes like a real recording (auto-report, post-call hook if configured).
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

# repo-root/packages/corella-live/dist — built by `npm run build` there.
_LIVE_CLIENT_DIST = Path(__file__).resolve().parents[2] / "packages" / "corella-live" / "dist"

app = FastAPI(title="Corella API test server")

_MANDATORY_HEADERS = [
    "x-corella-app-url",
    "x-corella-meeting-id",
    "x-corella-user-id",
    "x-corella-org-id",
]
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


@app.get("/corella-live/{asset_path:path}")
async def live_client_asset(asset_path: str):
    """ESM build of packages/corella-live so this page can import it
    without a bundler. Hook-integration tests never hit this route.
    """
    if not _LIVE_CLIENT_DIST.is_dir():
        return PlainTextResponse(
            "corella-live dist/ missing — run: cd packages/corella-live && npm install && npm run build",
            status_code=503,
        )
    root = _LIVE_CLIENT_DIST.resolve()
    target = (root / asset_path).resolve()
    if not target.is_file() or not target.is_relative_to(root):
        return PlainTextResponse("not found", status_code=404)
    media = "application/javascript" if target.suffix == ".js" else "text/plain"
    return FileResponse(target, media_type=media)


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
    if path == "list-call-types" and request.method == "POST":
        return await _list_call_types(request)

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


async def _list_call_types(request: Request) -> JSONResponse:
    """Same CORS reason as _start_test_call: GET /api/call-types with an
    API key from this page would preflight. Proxied so the dropdown can
    load real ids.
    """
    payload = await request.json()
    api_base = payload["api_base"].rstrip("/")
    api_key = payload["api_key"]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{api_base}/api/call-types",
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
  .config input, .config select { padding: 6px 8px; border: 1px solid #e4e4e1; border-radius: 6px; font-size: 12px; }
  .config input[type=text] { width: 220px; }
  .config select { min-width: 180px; }
  button { padding: 7px 14px; border-radius: 6px; border: 1px solid #0b1b33; background: #0b1b33; color: #fafaf9; font-size: 12px; cursor: pointer; }
  button:disabled { opacity: .4; cursor: default; }
  button.secondary { background: #fff; color: #0b1b33; }
  #status { font-size: 12px; color: #5b5f6b; margin-left: 8px; }
  #transcriptCol { flex: 2; min-width: 380px; }
  #copilotCol { flex: 1; min-width: 280px; }
  #transcript { height: 360px; overflow-y: auto; font-size: 13px; line-height: 1.5; border: 1px solid #e4e4e1; border-radius: 6px; padding: 10px; background: #fafaf9; }
  #transcript .line { display: flex; align-items: baseline; gap: 6px; }
  #transcript .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  #transcript .partial { opacity: .55; font-style: italic; }
  .label { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #8b8f99; margin: 14px 0 4px; }
  .label:first-child { margin-top: 0; }
  .gauges { margin-bottom: 4px; }
  .gauges:empty { display: none; }
  .cluster { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; width: 100%; }
  .cluster-cell { display: flex; flex-direction: column; align-items: center; min-width: 0; }
  .cluster-cell .ring { position: relative; width: 72px; height: 72px; }
  .cluster-cell .value { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: 400; font-family: Georgia, ui-serif, serif; line-height: 1; pointer-events: none; }
  .cluster-cell .face { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
  .cluster-cell .cap { margin-top: 8px; width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; font-weight: 500; line-height: 1; text-align: center; color: #8b8f99; }
  .cluster-cell .cap.word { color: #12141a; }
  #suggestion { font-size: 13px; }
  ul { margin: 4px 0; padding-left: 18px; font-size: 13px; }
  li.danger { color: #b3261e; }
</style>
</head>
<body>
<header>
  <h1>Corella API test server — live recording</h1>
  <p>Uses <code>corella-live</code> the same way an integrator's site would — this page is only UI. Meeting create is proxied here to skip CORS.</p>
</header>

<div class="config">
  <label>API base <input type="text" id="apiBase" value="http://localhost:8090"></label>
  <label>API key <input type="text" id="apiKey" placeholder="sk_live_..." size="30"></label>
  <label>Call type
    <select id="callTypeId">
      <option value="">Default type</option>
    </select>
  </label>
  <button type="button" id="loadTypesBtn" class="secondary">Load types</button>
  <button type="button" id="startBtn">Start</button>
  <button type="button" id="stopBtn" class="secondary" disabled>Stop</button>
  <span id="status">Idle.</span>
</div>

<div class="layout">
  <div class="col" id="transcriptCol">
    <div class="label">Transcript</div>
    <div id="transcript"></div>
  </div>
  <div class="col" id="copilotCol">
    <div class="gauges" id="gauges"></div>
    <div class="label">Suggestion</div>
    <div id="suggestion">Nothing yet.</div>
    <div class="label">Blockers</div>
    <ul id="blockers"><li>None.</li></ul>
    <div class="label">Action items</div>
    <ul id="actionItems"><li>None.</li></ul>
  </div>
</div>

<script type="module">
let session = null;
let lines = [];
let partials = { me: "", them: "" };
let lastCopilot = {};
let parseSentimentFn = (raw) => {
  if (typeof raw !== "string") return null;
  const set = ["Hostile","Tense","Frustrated","Skeptical","Neutral","Engaged","Positive","Enthusiastic"];
  const needle = raw.trim().toLowerCase();
  return set.find((v) => v.toLowerCase() === needle) || null;
};
let sentimentFillFn = (value) => {
  const set = ["Hostile","Tense","Frustrated","Skeptical","Neutral","Engaged","Positive","Enthusiastic"];
  const i = set.indexOf(value);
  return i < 0 ? 0 : i / (set.length - 1);
};

const ACCENT = "#0B1B33";
const TRACK = "#E4E4E1";
const SENTIMENT_COLOR = "#1D5A8C";
const SPEAKER_COLORS = ["#0B1B33", "#2A6A84", "#1F7A4D", "#9A6300", "#B3261E", "#5C4E79", "#3E6A58", "#8B8F99"];
const ANON_RE = /^(Speaker|Them) \\d+$/;

function assignSpeakerColors(labels) {
  const map = new Map();
  let nextOther = 1;
  const otherSlots = SPEAKER_COLORS.length - 1;
  for (const label of labels) {
    if (map.has(label)) continue;
    if (label === "Me") map.set(label, 0);
    else { map.set(label, 1 + ((nextOther - 1) % otherSlots)); nextOther += 1; }
  }
  return map;
}
function speakerColorHex(label, colors) {
  const index = colors && colors.has(label) ? colors.get(label) : (label === "Me" ? 0 : 1);
  return SPEAKER_COLORS[index % SPEAKER_COLORS.length];
}
function canonicalLabel(line) {
  const raw = line.speakerLabel;
  if (raw === "me" || raw === "Me") return "Me";
  if (raw === "them" || raw === "Them") return "Them";
  if (raw && raw !== "unknown") return raw;
  if (line.channel === "me") return "Me";
  if (line.channel === "them") return "Them";
  return "Unknown";
}
function isAnonymous(label) {
  return label === "Unknown" || label === "Them" || label === "Speaker" || ANON_RE.test(label);
}
function lineDisplayLabels(rows) {
  const aliases = new Map();
  let nextN = 1;
  return rows.map((line) => {
    const raw = canonicalLabel(line);
    if (raw === "Me" || !isAnonymous(raw)) return raw;
    const key = (line.channel || "unknown") + ":" + raw;
    if (!aliases.has(key)) { aliases.set(key, "Speaker " + nextN); nextN += 1; }
    return aliases.get(key);
  });
}
function speakerShareFromLines(rows) {
  const labeled = lineDisplayLabels(rows);
  const msByLabel = new Map();
  const order = [];
  for (let i = 0; i < rows.length; i++) {
    const duration = Math.max(0, (rows[i].end_ms || 0) - (rows[i].start_ms || 0));
    if (duration <= 0) continue;
    const label = labeled[i];
    if (!msByLabel.has(label)) { order.push(label); msByLabel.set(label, 0); }
    msByLabel.set(label, msByLabel.get(label) + duration);
  }
  const total = order.reduce((sum, label) => sum + msByLabel.get(label), 0);
  if (!total || !order.length) return null;
  const pcts = order.map((label) => Math.round((msByLabel.get(label) / total) * 100));
  pcts[pcts.length - 1] += 100 - pcts.reduce((sum, pct) => sum + pct, 0);
  const colors = assignSpeakerColors(order);
  return order.map((label, i) => ({ label, pct: pcts[i], color: speakerColorHex(label, colors) })).filter((row) => row.pct > 0);
}

for (const id of ["apiBase", "apiKey"]) {
  const saved = localStorage.getItem("corella_test_" + id);
  if (saved) document.getElementById(id).value = saved;
  document.getElementById(id).addEventListener("change", (e) => localStorage.setItem("corella_test_" + id, e.target.value));
}

function fillCallTypes(types, selectedId) {
  const sel = document.getElementById("callTypeId");
  const keep = selectedId || sel.value || localStorage.getItem("corella_test_callTypeId") || "";
  sel.innerHTML = '<option value="">Default type</option>';
  for (const t of types) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = t.name + (t.is_default ? " (default)" : "");
    sel.appendChild(opt);
  }
  if (keep && [...sel.options].some((o) => o.value === keep)) sel.value = keep;
}

async function loadTypes() {
  const apiBase = document.getElementById("apiBase").value.replace(/\\/$/, "");
  const apiKey = document.getElementById("apiKey").value.trim();
  if (!apiKey) { setStatus("Paste an API key first, then Load types."); return; }
  const resp = await fetch("/list-call-types", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_base: apiBase, api_key: apiKey }),
  });
  const body = await resp.json();
  if (!resp.ok) {
    setStatus("Couldn't load call types: " + (body.error || resp.status));
    return;
  }
  fillCallTypes(body);
  setStatus("Loaded " + body.length + " call type" + (body.length === 1 ? "" : "s") + ".");
}

document.getElementById("callTypeId").addEventListener("change", (e) => {
  localStorage.setItem("corella_test_callTypeId", e.target.value);
});
document.getElementById("loadTypesBtn").addEventListener("click", loadTypes);

function setStatus(s) { document.getElementById("status").textContent = s; }
function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; }

function sentimentFaceSvg(kind) {
  const eyes = '<circle cx="11.5" cy="13" r="1.5" fill="#12141A"/><circle cx="20.5" cy="13" r="1.5" fill="#12141A"/>';
  const m = 'fill="none" stroke="#12141A" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round"';
  const b = 'fill="none" stroke="#12141A" stroke-width="1.7" stroke-linecap="round"';
  const faces = {
    Hostile: `<path d="M9 9.4 L13.3 11.6" ${b}/><path d="M23 9.4 L18.7 11.6" ${b}/><path d="M11 21.2 H21" ${m}/>`,
    Tense: `<path d="M9.6 10.2 L13.2 11.5" ${b}/><path d="M22.4 10.2 L18.8 11.5" ${b}/><path d="M11.2 21 Q16 19.4 20.8 21" ${m}/>`,
    Frustrated: `<path d="M10.4 22.2 Q16 16.6 21.6 22.2" ${m}/>`,
    Skeptical: `<path d="M11.2 21.4 Q16 18.6 20.8 21.4" ${m}/>`,
    Neutral: `<path d="M11 20.4 H21" ${m}/>`,
    Engaged: `<path d="M11.4 19.2 Q16 23 20.6 19.2" ${m}/>`,
    Positive: `<path d="M10.4 18.6 Q16 24.4 21.6 18.6" ${m}/>`,
    Enthusiastic: `<path d="M10 18 A 6 7.2 0 0 0 22 18 Z" ${m}/>`,
  };
  return `<svg width="36" height="36" viewBox="0 0 32 32" aria-hidden="true">${eyes}${faces[kind] || faces.Neutral}</svg>`;
}

function clusterHtml(score, sentiment, share) {
  const SIZE = 72, STROKE = 4, CX = 36;
  const R = (SIZE - STROKE) / 2;
  const C = 2 * Math.PI * R;
  const parsed = parseSentimentFn(sentiment);
  const shownSentiment = parsed || "Neutral";
  const fill = sentimentFillFn(shownSentiment);
  const clamped = score == null ? 50 : Math.max(0, Math.min(100, score));
  const slices = (share || []).filter((s) => s.pct > 0);

  const track = `<circle cx="${CX}" cy="${CX}" r="${R}" fill="none" stroke="${TRACK}" stroke-width="${STROKE}"/>`;
  const sentArc = fill <= 0 ? "" : `<circle cx="${CX}" cy="${CX}" r="${R}" fill="none" stroke="${SENTIMENT_COLOR}" stroke-width="${STROKE}" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - fill)}"/>`;
  const scoreArc = `<circle cx="${CX}" cy="${CX}" r="${R}" fill="none" stroke="${ACCENT}" stroke-width="${STROKE}" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - clamped / 100)}"/>`;
  let shareArcs = "";
  let cursor = 0;
  for (const s of slices) {
    const seg = Math.max(C * (s.pct / 100), 1);
    shareArcs += `<circle cx="${CX}" cy="${CX}" r="${R}" fill="none" stroke="${s.color}" stroke-width="${STROKE}" stroke-dasharray="${seg} ${C - seg}" stroke-dashoffset="${-cursor}"><title>${escapeHtml(s.label)} ${s.pct}%</title></circle>`;
    cursor += C * (s.pct / 100);
  }

  const mePct = slices.find((s) => s.label === "Me");

  return `<div class="cluster">
    <div class="cluster-cell">
      <div class="ring">
        <svg width="${SIZE}" height="${SIZE}" viewBox="0 0 ${SIZE} ${SIZE}" style="transform:rotate(-90deg)">${track}${scoreArc}</svg>
        <div class="value">${clamped}</div>
      </div>
      <div class="cap">Score</div>
    </div>
    <div class="cluster-cell">
      <div class="ring">
        <svg width="${SIZE}" height="${SIZE}" viewBox="0 0 ${SIZE} ${SIZE}" style="transform:rotate(-90deg)">${track}${sentArc}</svg>
        <div class="face" title="${escapeHtml(shownSentiment)}">${sentimentFaceSvg(shownSentiment)}</div>
      </div>
      <div class="cap">Sentiment</div>
    </div>
    <div class="cluster-cell">
      <div class="ring">
        <svg width="${SIZE}" height="${SIZE}" viewBox="0 0 ${SIZE} ${SIZE}" style="transform:rotate(-90deg)">${track}${shareArcs}</svg>
        ${mePct ? `<div class="value">${mePct.pct}</div>` : ""}
      </div>
      <div class="cap">Share</div>
    </div>
  </div>`;
}

function renderGauges() {
  const fromCopilot = (lastCopilot.speaker_share || []).filter((s) => s.label && s.pct > 0);
  const copilotColors = assignSpeakerColors(fromCopilot.map((s) => s.label));
  const share = speakerShareFromLines(lines) || fromCopilot.map((s) => ({
    label: s.label, pct: s.pct, color: speakerColorHex(s.label, copilotColors),
  }));
  document.getElementById("gauges").innerHTML = clusterHtml(
    lastCopilot.coach_score ?? null,
    lastCopilot.sentiment,
    share,
  );
}

function renderTranscript() {
  const labels = lineDisplayLabels(lines);
  const colors = assignSpeakerColors(labels);
  let html = lines.map((s, i) => {
    const label = labels[i];
    return `<div class="line"><span class="dot" style="background:${speakerColorHex(label, colors)}"></span><b>${escapeHtml(label)}</b>: ${escapeHtml(s.text)}</div>`;
  }).join("");
  for (const ch of ["me", "them"]) {
    if (partials[ch]) html += `<div class="line partial"><b>${ch}</b>: ${escapeHtml(partials[ch])}…</div>`;
  }
  const el = document.getElementById("transcript");
  el.innerHTML = html || '<span style="color:#8b8f99">(waiting for speech…)</span>';
  el.scrollTop = el.scrollHeight;
  renderGauges();
}

function renderCopilot(msg) {
  lastCopilot = msg || {};
  renderGauges();
  document.getElementById("suggestion").textContent = lastCopilot.suggestion || "Nothing yet.";
  const blockers = lastCopilot.blockers || [];
  document.getElementById("blockers").innerHTML = blockers.length
    ? blockers.map((b) => `<li class="danger">${escapeHtml(b)}</li>`).join("")
    : "<li>None.</li>";
  const items = lastCopilot.action_items || [];
  document.getElementById("actionItems").innerHTML = items.length
    ? items.map((a) => `<li>${escapeHtml(a)}</li>`).join("")
    : "<li>None.</li>";
}

async function loadClient() {
  try {
    const mod = await import("/corella-live/index.js");
    if (typeof mod.parseSentiment === "function") parseSentimentFn = mod.parseSentiment;
    if (typeof mod.sentimentFill === "function") sentimentFillFn = mod.sentimentFill;
    return mod;
  } catch (e) {
    setStatus("corella-live is not built. From the repo: cd packages/corella-live && npm install && npm run build");
    throw e;
  }
}

async function start() {
  const apiBase = document.getElementById("apiBase").value.replace(/\\/$/, "");
  const apiKey = document.getElementById("apiKey").value.trim();
  const callTypeId = document.getElementById("callTypeId").value.trim() || null;
  if (!apiKey) { setStatus("Paste an API key first (Settings -> API keys in the real app)."); return; }

  document.getElementById("startBtn").disabled = true;
  lines = [];
  partials = { me: "", them: "" };
  lastCopilot = {};
  renderTranscript();
  renderCopilot({});
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
  setStatus(`Meeting ${created.id} created — connecting via corella-live…`);

  let CorellaLive;
  try {
    ({ CorellaLive } = await loadClient());
    session = await CorellaLive.connect({ apiBase, apiKey, meetingId: created.id });
  } catch (e) {
    setStatus("Connect failed: " + (e && e.message ? e.message : e));
    document.getElementById("startBtn").disabled = false;
    return;
  }

  session.onTranscript((next) => { lines = next; renderTranscript(); });
  session.onPartial((p) => { partials = p; renderTranscript(); });
  session.onCopilot(renderCopilot);
  session.onCopilotUnavailable(() => setStatus("Recording (no LLM connected for this account — no live suggestions)."));
  session.onClose(({ code, reason }) => {
    document.getElementById("stopBtn").disabled = true;
    document.getElementById("startBtn").disabled = false;
    if (code === 4410) setStatus("Duration cap (4410) — meeting still finalizes.");
    else setStatus(reason ? `Disconnected (${code}): ${reason}` : "Disconnected.");
  });

  document.getElementById("stopBtn").disabled = false;
  setStatus("Recording — speak into your mic.");
  try {
    await session.startMic();
  } catch (e) {
    setStatus("Mic failed: " + (e && e.message ? e.message : e));
  }
}

function stop() {
  session?.stop();
  document.getElementById("stopBtn").disabled = true;
  document.getElementById("startBtn").disabled = false;
  setStatus("Stopping…");
}

document.getElementById("startBtn").addEventListener("click", start);
document.getElementById("stopBtn").addEventListener("click", stop);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9199)
