# Corella API

A reference for integrating an external system with Corella: creating and reading meetings, streaming a live recording from outside the browser, and configuring per-call-type API hooks that fire before and after a call.

Everything below sits alongside the interactive schema at `/docs` (FastAPI's auto-generated Swagger UI) — this document adds the parts that schema can't show: the WebSocket protocol, the pre/post call-type hook mechanism, and worked examples.

## Authentication

Two credential types work interchangeably as a bearer token on every authenticated request:

- **A JWT** from `POST /api/auth/login` — what the browser app uses, short-lived (`access_token_expire_minutes`, default 24h).
- **An API key** — a long-lived credential for external/machine access, created in **Settings → API keys**. Looks like `sk_live_<random>`. Shown in full exactly once, at creation — only its hash is ever stored, so if you lose it you create a new one.

```
Authorization: Bearer sk_live_<your-key>
```

An API key acts **as the user who created it** — it can do anything that user could do through the browser, scoped to their own account (their meetings, their groups' shared knowledge base, etc.). It does *not* currently support fine-grained scopes; treat it like a password.

API keys are accepted on: `POST /api/meetings`, `GET /api/meetings/{id}`, `GET /api/meetings/{id}/transcript`, `GET /api/meetings/{id}/insights`, `POST /api/meetings/{id}/report`, and the live WebSocket (`/ws/meetings/{id}/live`). Every other endpoint still requires a JWT (a real browser login) — API keys are scoped to the external-integration surface, not the whole app.

## REST reference

Base URL is your instance's API origin (e.g. `http://localhost:8090` in local dev — see `docker-compose.yml`).

### Create (and start) a meeting

```
POST /api/meetings
Authorization: Bearer <token or api key>
Content-Type: application/json

{ "title": "Sales call with Acme", "call_type_id": null }
```

`call_type_id` is optional — omit it (or send `null`) to use whichever call type is currently marked default. There's no separate "start" call: a meeting is created with `status: "recording"` immediately, and if its call type has a pre-call hook configured, that hook fires synchronously here (see [Pre/post call-type hooks](#prepost-call-type-hooks) below) before the response comes back.

Response (`201`):

```json
{
  "id": "3fa2...", "title": "Sales call with Acme", "status": "recording",
  "call_type": { "id": "...", "name": "Sales", "slug": "sales", "is_default": false },
  "started_at": null, "ended_at": null, "duration_seconds": null,
  "has_audio": false, "processing_error": null,
  "summary": null, "key_topics": null, "sentiment": null, "notable_quotes": null,
  "coach_score": null, "estimated_cost_usd": null,
  "created_at": "2026-09-08T12:00:00Z", "owner_id": "...", "owner_name": "..."
}
```

Use the returned `id` to open the live WebSocket (below).

### Read a meeting / its results

```
GET /api/meetings/{id}                     -> MeetingRead (as above)
GET /api/meetings/{id}/transcript          -> [{ id, speaker_label, linked_user_id, channel, start_ms, end_ms, text }, ...]
GET /api/meetings/{id}/insights            -> [{ id, at_ms, suggestion, blockers: [string], coach_score }, ...]
POST /api/meetings/{id}/report             -> generates (or regenerates) the summary/report synchronously
```

`insights` is the live-copilot timeline — every cycle the in-call coaching assistant produced, timestamp-ordered against the transcript's own clock (`at_ms` lines up with `transcript[].start_ms`/`end_ms`). A normal auto-generated report already fires once a recording finishes; `POST /report` is there if you want to force a fresh one.

## WebSocket: streaming a live recording

This is the same protocol the browser app itself uses to record — an external caller uses it identically. No separate "streaming API" exists; this connection *is* the stream.

```
ws(s)://<api-host>/ws/meetings/{meeting_id}/live
```

1. **Connect**, then immediately send one text frame to authenticate — the very first message, before anything else:
   ```json
   {"type": "auth", "token": "<JWT>"}
   ```
   or, for a machine caller:
   ```json
   {"type": "auth", "api_key": "sk_live_..."}
   ```
   (A JWT/API key is never put in the URL or a header — a query string would land in access logs, and browsers can't set custom WebSocket headers at all, so the protocol carries auth as its own first message instead.)

2. On success, the server sends `{"type": "ready"}`. If `meeting_id` doesn't exist or doesn't belong to the caller, isn't in `recording` status, or auth fails, the connection is closed with one of:
   | Code | Meaning |
   |---|---|
   | `4401` | Auth timed out, missing, or invalid (bad JWT/API key) |
   | `4404` | Meeting not found, or not owned by the caller |
   | `4409` | Meeting isn't in `recording` status (already stopped/finalized) |

3. **Stream audio** as binary frames, one frame per chunk: **byte 0** is the channel selector (`0x00` = "me", `0x01` = "them"), the remaining bytes are raw PCM16LE, mono, 16kHz.
   ```
   [ channel_byte ][ pcm16le audio bytes... ]
   ```
   Two independent channels exist ("me"/"them") so a two-sided call can be diarized without relying on voice separation alone — a phone-bridge integration would typically only ever send channel `0`.

4. **Stop** by sending `{"type": "stop"}`, or simply **close the connection** — either way triggers the same finalize path (mix/save audio, transcribe anything still buffered, generate the report, and — if configured — fire the call type's post-call hook). A graceful `stop` gets one last `{"type": "stopped"}` acknowledgement before the socket closes; a bare disconnect finalizes just the same, just without that ack.

### Messages the server sends

| `type` | Shape | When |
|---|---|---|
| `ready` | `{}` | Auth succeeded, safe to start sending audio |
| `copilot_unavailable` | `{}` | No LLM provider connected for this account — live suggestions are off, transcription still works |
| `transcript` | `{ segment: {id, channel, start_ms, end_ms, text} }` | A committed (final) transcript segment |
| `partial_transcript` | `{ channel, text }` | A disposable, more-frequent live preview — replaced by the next `transcript`/`partial_transcript` for that channel, never persisted |
| `copilot` | `{ suggestion, blockers: [string], action_items: [string], coach_score }` | One live-coaching cycle's result (also persisted — see `GET /insights`) |
| `diarization_update` / `speaker_hint` | `{ is_snapshot, removed_segment_ids: [string], segments: [DiarizedSegment] }` | Speaker labels resolving/changing as more audio arrives |
| `stopped` | `{}` | Acknowledges a graceful client-sent `stop` |

There's no separate PCM sample-rate/encoding negotiation — 16-bit PCM, mono, 16000 Hz is the one supported format; resample before sending if your source audio is anything else.

## Pre/post call-type hooks

Each call type (**Admin → Call types**) can call an external API **before** a call of that type starts and/or **after** it finishes — for pulling context into the conversation, and for pushing the finished result out to another system.

### Before the call (pre-call)

Fires synchronously from `POST /api/meetings`, before the response comes back — bounded by a short timeout (`pre_call_timeout_seconds`, default 5s) so a slow/broken endpoint never blocks or fails meeting creation; a failure here just means no extra context that time, logged server-side.

- **Method**: any (`GET` by default — it's usually a lookup).
- **URL / headers / body**: fully admin-configurable. Headers support an encrypted `Authorization`-style secret. The body supports `{{placeholder}}` substitution too, for a `POST`/`PUT`/`PATCH` pre-call that needs to send something (e.g. `{"lookup_name": "{{owner_name}}"}` for a CRM query) — but only meeting-level fields are available here, since no transcript or report exists yet at this point: `{{meeting_id}}`, `{{owner_id}}`, `{{owner_name}}`, `{{title}}`, `{{call_type}}`, `{{status}}`, `{{created_at}}`. (The full placeholder set below, including `{{transcript}}`/`{{full_payload}}`/etc., is post-call only.)
- **"Use response as conversation context"**: when enabled, the response body is stored on the meeting and fed into the live copilot's prompt for every coaching cycle during the call — *alongside*, not instead of, the group's own knowledge base. Capped at `pre_call_context_max_chars` (default 20,000 characters). This is independent of whether a body template is set — the pre-call still fires either way; this flag only controls whether its *response* becomes context. With this off, a pre-call is still useful purely as a side effect (e.g. notifying another system a call started).

### After the call (post-call)

Fires once, right after a call's report finishes auto-generating.

- **Method / URL / headers**: same shape as pre-call (defaults to `POST`).
- **Body**: either a hand-written JSON template with `{{placeholder}}` tokens, or — with **"Send everything"** enabled — the full structured payload below, no template needed.

### The three mandatory headers

Every pre-call and post-call request carries these, **always**, regardless of what the admin configured — they cannot be overridden by a custom header of the same name (the real values are applied last, after any custom headers are merged in):

| Header | Value |
|---|---|
| `X-Corella-App-Url` | This Corella instance's own public URL (`public_app_url` setting) — identifies *which* deployment the request came from |
| `X-Corella-Meeting-Id` | The meeting's UUID |
| `X-Corella-User-Id` | The meeting owner's user UUID |

### Placeholder reference (post-call body templates)

| Placeholder | Value |
|---|---|
| `{{meeting_id}}`, `{{owner_id}}`, `{{owner_name}}` | Identity |
| `{{title}}`, `{{call_type}}`, `{{status}}` | Basics |
| `{{summary}}`, `{{key_topics}}`, `{{sentiment}}`, `{{notable_quotes}}` | Report content |
| `{{coach_score}}`, `{{estimated_cost_usd}}`, `{{talk_ratio}}` | Report metrics (`talk_ratio` is `{"me": <pct>, "them": <pct>}`) |
| `{{action_items}}` | `[{"text": "...", "status": "open"\|"done"}, ...]` |
| `{{copilot_insights}}` | `[{"at_ms": 12000, "suggestion": "...", "blockers": [...], "coach_score": 74}, ...]` — the same live-coaching timeline `GET /insights` returns |
| `{{transcript}}` | Full transcript, `"Me: ...\nThem: ...\n..."` |
| `{{created_at}}`, `{{started_at}}`, `{{ended_at}}`, `{{duration_seconds}}` | Timing |
| `{{full_payload}}` | The entire structured payload below, as one embedded JSON object — equivalent to turning "Send everything" on, but usable inline in a hand-written template |

A placeholder inside a quoted string (`"summary": "{{summary}}"`) is substituted as a properly JSON-escaped string; an array/object/number placeholder (`"key_topics": {{key_topics}}`) should be placed *outside* quotes — it substitutes as real JSON.

### The full payload shape ("Send everything" / `{{full_payload}}`)

```json
{
  "meeting_id": "3fa2...", "owner_id": "...", "owner_name": "Jane Doe",
  "title": "Sales call with Acme", "call_type": "Sales", "status": "ready",
  "summary": "...", "key_topics": ["Pricing", "Timeline"], "sentiment": "Positive",
  "notable_quotes": ["..."],
  "coach_score": 82, "estimated_cost_usd": 0.0341, "talk_ratio": {"me": 58, "them": 42},
  "action_items": [{"text": "Send proposal by Friday", "status": "open"}],
  "copilot_insights": [
    {"at_ms": 42000, "suggestion": "Address the pricing objection directly", "blockers": ["Pricing concern raised"], "coach_score": 71}
  ],
  "transcript": "Me: ...\nThem: ...",
  "created_at": "2026-09-08T12:00:00Z", "started_at": "2026-09-08T12:00:05Z",
  "ended_at": "2026-09-08T12:31:20Z", "duration_seconds": 1875
}
```

### Example: receiving a post-call payload (Node/Express)

```js
app.post("/corella-webhook", express.json(), (req, res) => {
  const meetingId = req.header("X-Corella-Meeting-Id");
  const userId = req.header("X-Corella-User-Id");
  const { summary, coach_score, action_items, copilot_insights } = req.body;
  // ...
  res.sendStatus(200);
});
```

### Example: a pre-call context lookup

Corella calls `GET https://your-crm.example.com/lookup?...` with the three mandatory headers attached; your endpoint returns plain text or JSON, which becomes the "External context" block in every live-coaching prompt for that call if "use as context" is on.

### Testing your integration: `server/scripts/api_test_server.py`

A small FastAPI app, committed in this repo specifically to stand in for "the other system" on the receiving end of a pre/post call-type hook while you build one — no need to have your real receiving endpoint ready yet, or to guess whether Corella is sending what you think it is.

Run it:

```
cd server && .venv/bin/uvicorn scripts.api_test_server:app --port 9199 --reload
```

Then in **Admin → Call types**, point a hook at it from inside the `api`/`worker` containers via `host.docker.internal` (not `localhost` — that resolves to the container itself, not your host machine):

```
http://host.docker.internal:9199/pre     # any path containing "pre"  -> 200, a fake CRM-lookup-shaped text body
http://host.docker.internal:9199/post    # anything else             -> 200, {"ok": true}
http://host.docker.internal:9199/anyfail # any path containing "fail" -> 500, for testing graceful failure
http://host.docker.internal:9199/anyslow # any path containing "slow" -> sleeps 8s, for testing the timeout path
```

Every request it receives is logged to its own console (method, the three mandatory headers checked off explicitly, and the body) and kept in memory — `GET http://localhost:9199/requests` to inspect everything received so far as JSON, `DELETE` to clear it between runs.

It's also exercised as real test infrastructure, not just a manual aid: `server/tests/test_call_hooks_integration.py` starts this exact app as a real subprocess and asserts against real loopback HTTP responses — covering both hooks in both their "special" (`pre_call_use_as_context` / `post_call_send_full_payload`) and "regular" (plain fetch / custom template) modes, plus the graceful-failure and timeout paths — complementing `test_call_hooks.py`'s monkeypatched-`httpx` unit tests of the same logic.

## Errors

REST errors are standard FastAPI/Pydantic shape: `{"detail": "..."}` with the appropriate 4xx/5xx status (`401` invalid/missing credentials, `403` insufficient role, `404` not found or not owned, `409` conflict, `422` validation). WebSocket errors are close codes (`4401`/`4404`/`4409`, see above) — there's no in-band `{"type":"error",...}` message today; a failed connection is always a closed connection with a reason string.
