# Corella API

How to integrate an external system with Corella. There are **three** integration types — they compose, they are not alternatives:

1. **Inbound REST (API keys)** — create and read meetings as a specific user, without a browser login.
2. **Live WebSocket** — stream a recording in real time. Same protocol the browser app uses; an API key authenticates it.
3. **Outbound call-type hooks** — Corella calls *your* HTTP endpoint before a call of a given type starts and/or after it finishes.

Interactive request/response schemas for every HTTP route (including browser-only ones) live at `/docs` (FastAPI's Swagger UI). This document is the integration contract: which credentials work where, the WebSocket protocol, hook payloads, and every quirk that the schema cannot show.

Base URL is your instance's API origin. In local Docker that is `http://localhost:8090` (host port remap in `docker-compose.yml`; the container itself still listens on 8000). Unauthenticated liveness: `GET /api/health` → `{"status":"ok"}`.

---

## The three types at a glance

| | Direction | Auth | When |
|---|---|---|---|
| **API key REST** | You → Corella | `Authorization: Bearer sk_live_…` | Create a meeting, poll results |
| **Live WebSocket** | You ↔ Corella | First text frame `{"type":"auth","api_key":"…"}` | Stream audio, receive transcript/copilot live |
| **Call-type hooks** | Corella → you | *Your* endpoint; Corella always sends three `X-Corella-*` headers | Pre-call lookup / post-call push |

A typical phone-bridge or CRM integration uses all three: REST to create the meeting, WebSocket to stream audio, a post-call hook to push the finished report back into the CRM. A "just notify us when a call ends" integration only needs the hook. A "pull the transcript later" integration only needs the API key.

---

## Authentication

Two credential types exist. They are **not** interchangeable on every route.

### JWT (browser login)

`POST /api/auth/login` returns a short-lived JWT (`access_token_expire_minutes`, default 24h, HS256). This is what the web app uses. It unlocks **the whole API** (settings, admin, knowledge base, uploads, deletes, …).

### API key (machine / external)

A long-lived credential created in **Settings → API keys** (or `POST /api/settings/api-keys` — that management surface itself still requires a JWT). Looks like `sk_live_<random>`. Shown in full **exactly once**, at creation — only a sha256 hash is stored, so a lost key cannot be recovered; create a new one.

```
Authorization: Bearer sk_live_<your-key>
```

An API key acts **as the user who created it**. No fine-grained scopes — treat it like a password for that account. It can only reach the **integration surface** below, not the rest of the app. Using it on a JWT-only route returns `401`.

The `sk_live_` prefix is how the server tells a key apart from a JWT **without** attempting a JWT decode first. Do not strip it; a key that does not start with `sk_live_` is treated as a (then-invalid) JWT.

`last_used_at` on the key is bumped on every successful REST or WebSocket authentication.

### Where an API key is accepted

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/meetings` | Create (and start) a meeting |
| `GET` | `/api/meetings/{id}` | Meeting + report fields. **Group-visible** — see [Access](#access-what-the-key-can-see) |
| `GET` | `/api/meetings/{id}/transcript` | Owner or admin only |
| `GET` | `/api/meetings/{id}/insights` | Owner or admin only |
| `POST` | `/api/meetings/{id}/report` | Owner only; synchronous regenerate |
| WS | `/ws/meetings/{id}/live` | Live recording |

Everything else is JWT-only. In particular an API key **cannot**:

- List meetings (`GET /api/meetings`), search, group/all lists
- List call types (`GET /api/call-types`) — you must already know a `call_type_id`, or omit it and take the instance default
- Upload / download audio, delete a meeting, read or toggle action items
- Manage API keys, providers, preferences, knowledge base, or admin resources

### Managing keys (JWT / Settings UI)

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/settings/api-keys` | — (never includes the plaintext key) |
| `POST` | `/api/settings/api-keys` | `{ "name": "Zapier", "max_duration_minutes": 60 }` |
| `PATCH` | `/api/settings/api-keys/{id}` | `{ "max_duration_minutes": 90 }` — duration only; rename is not supported |
| `DELETE` | `/api/settings/api-keys/{id}` | Immediate revoke. Meetings that used the key stay; their `api_key_name` becomes `null` (`ON DELETE SET NULL`) |

`max_duration_minutes` defaults to **60**, allowed range **1–480**. There is no "unlimited". Browser/JWT live sessions are **not** capped. REST calls are not capped either — the bound is only on the live WebSocket. A `PATCH` mid-call does not move the goalposts of a session that is already running; the cap is snapshotted at connect.

### Max duration (`4410`)

When an API-key-authenticated live session reaches that key's cap, the server closes the socket with code **`4410`** and reason `API key max meeting duration reached`. The meeting still **finalizes** (same path as `stop` / disconnect): audio is saved, leftovers are transcribed, status goes `processing` → `ready`, auto-report and post-call hook still run. This is not an auth failure.

---

## REST: meetings

### Create (and start)

```
POST /api/meetings
Authorization: Bearer <jwt or api key>
Content-Type: application/json

{ "title": "Sales call with Acme", "call_type_id": null }
```

- `title` defaults to `"Untitled meeting"` if omitted.
- `call_type_id` is optional. Omit it or send `null` to use whichever call type is currently marked default. Unknown id → `422`. If the instance has no call types at all, the meeting is created untyped (`call_type: null`) and no pre-call fires.
- There is **no separate "start" call**. The meeting is created with `status: "recording"` immediately.
- If that call type has a pre-call hook, it fires **synchronously here**, before the `201` comes back — bounded by `pre_call_timeout_seconds` (default 5s). A slow/broken hook never fails or delays creation past that timeout; you just get no extra context that time.
- Creating with an API key does **not** mark the meeting as "via API". That stamp (`api_key_name` on later reads) is set only when a live WebSocket actually authenticates with a key. You can create with a JWT and stream with a key (badge appears), or create with a key and record in the browser (no badge).

Response (`201`) — `MeetingRead`:

```json
{
  "id": "3fa2...", "title": "Sales call with Acme", "status": "recording",
  "call_type": { "id": "...", "name": "Sales", "slug": "sales", "is_default": false },
  "started_at": null, "ended_at": null, "duration_seconds": null,
  "has_audio": false, "processing_error": null,
  "summary": null, "key_topics": null, "sentiment": null, "notable_quotes": null,
  "coach_score": null, "estimated_cost_usd": null,
  "created_at": "2026-09-08T12:00:00Z", "owner_id": "...", "owner_name": "...",
  "api_key_name": null
}
```

`started_at` stays `null` until a live WebSocket actually connects. Use `id` to open the WebSocket.

### Read a meeting / its results

```
GET  /api/meetings/{id}              → MeetingRead (as above)
GET  /api/meetings/{id}/transcript   → [{ id, speaker_label, linked_user_id, channel, start_ms, end_ms, text }, ...]
GET  /api/meetings/{id}/insights     → [{ id, at_ms, suggestion, blockers: [string], coach_score }, ...]
POST /api/meetings/{id}/report       → generates (or regenerates) the summary/report synchronously
```

**Transcript.** `channel` is `"me"` | `"them"` | `"unknown"`. `speaker_label` may still be `null` while same-room diarization catches up (or permanently, for a voice that never accumulated enough speech). `linked_user_id` is set only when the label resolved to an enrolled account — render `"Me"` only when it equals the viewing user's id.

**Insights.** The persisted live-copilot timeline, timestamp-ordered. `at_ms` lines up with `transcript[].start_ms` / `end_ms`. This shape does **not** include `action_items` — those on the live `copilot` WebSocket message are per-cycle suggestions; the durable open/done action items live on the report (`POST /report` / post-call payload).

**`POST /report`.** Owner-only (not even an admin of someone else's meeting). Requires an LLM provider connected for that account (`422` otherwise). Synchronous. A normal auto-generated report already fires once a recording finishes; this is for a forced refresh. **It does not fire the post-call hook** — regenerating is not "the conversation ending" a second time.

### Meeting status

| `status` | Meaning |
|---|---|
| `recording` | Created, live WebSocket may connect. Only one live connection at a time. |
| `processing` | Socket ended; mix/transcribe leftovers running in the background. |
| `ready` | Audio saved. Auto-report is dispatched fire-and-forget — `summary` may still be `null` for a few seconds; poll. |
| `failed` | Finalize or upload-processing blew up; `processing_error` has a truncated reason. |

You cannot resume a meeting. Once it leaves `recording`, a WebSocket reconnect is `4409`. Create a new meeting.

---

## Access: what the key can see

Same rules as the browser, because the key *is* that user.

| Resource | Owner | Same group | Admin |
|---|---|---|---|
| `GET /meetings/{id}` (report fields) | yes | yes | yes |
| Transcript / insights | yes | **no** | yes |
| `POST /report` | yes | no | **no** (no admin override on writes) |
| Live WebSocket | yes | no | no (must be the owner) |

Group membership never grants the raw recording. `404` is used for both missing and forbidden (no `403` on these reads), so you cannot probe whether an id exists.

---

## WebSocket: streaming a live recording

This is the same protocol the browser app itself uses. There is no separate "streaming API"; this connection *is* the stream.

```
ws(s)://<api-host>/ws/meetings/{meeting_id}/live
```

CORS does not apply to WebSockets the way it does to `fetch`. REST create from a browser origin that is not in `CORS_ORIGINS` will fail preflight; server-to-server REST does not care. The test harness proxies create for that reason (see below); the WebSocket itself is opened directly.

### Handshake

1. **Connect**, then immediately send **one text frame** to authenticate — the very first message, within 5 seconds (`AUTH_TIMEOUT_SECONDS`), before anything else:

   ```json
   {"type": "auth", "token": "<JWT>"}
   ```

   or, for a machine caller:

   ```json
   {"type": "auth", "api_key": "sk_live_..."}
   ```

   A JWT/API key is never put in the URL or a header. A query string would land in access logs, and browsers cannot set custom WebSocket headers, so the protocol carries auth as its own first message.

2. On success the server sends `{"type": "ready"}`. If that account has no LLM provider connected, it then also sends `{"type": "copilot_unavailable"}` — transcription still works; live coaching is off.

3. If anything is wrong, the connection is **closed** (no in-band `{"type":"error"}` today):

   | Code | Meaning |
   |---|---|
   | `4401` | Auth timed out (no first frame in 5s), missing, or invalid (bad JWT / API key) |
   | `4404` | Meeting not found, or not owned by the caller |
   | `4409` | Meeting isn't in `recording` (already stopped/finalized), **or** another connection is already recording it |
   | `4410` | This API key's max live-session duration was reached — meeting still finalizes |

   `4409` for "already being recorded" is a Redis lock (`SET NX`, 30s TTL, renewed every 10s while the session is alive). Two tabs, a retried API client, or the owner opening Corella's live page while an integration is streaming the same meeting: the second connection is rejected; the first is unaffected. After a crash the lock expires on its own TTL rather than wedging the meeting forever.

### Audio frames

Binary frames, one per chunk:

```
[ channel_byte ][ pcm16le audio bytes... ]
```

- Byte 0: `0x00` = `"me"`, `0x01` = `"them"`. Anything else is ignored.
- Rest: raw **PCM16LE, mono, 16 kHz**. No codec, no sample-rate negotiation — resample before sending if your source is anything else.
- Two independent channels so a two-sided call can be diarized without relying on voice separation alone. A phone-bridge integration typically only ever sends channel `0` (`me`).

### Stop vs disconnect vs duration cap

All three **finalize**: leftovers are flushed, Deepgram streams closed, meeting marked `processing`, mix/save audio, transcribe remaining buffer, status `ready`, auto-report dispatched, post-call hook if configured.

| How it ends | Client sees | Finalize? |
|---|---|---|
| Send `{"type":"stop"}` | `{"type":"stopped"}`, then close | yes |
| Close the socket / tab / process | nothing (socket already gone) | yes |
| Cap hit (`4410`) | close code `4410` | yes |
| Server crash mid-session | — | **no** — meeting can sit at `recording` until a human/ intervening process deals with it; the Redis lock expires in ~30s so a *new* connection to the same meeting could be attempted, but status is still `recording` only if finalize never ran |

Closing the Corella web UI without pressing Stop is the same as a bare disconnect: React cleanup calls `ws.close()`, the server finalizes. A half-open TCP hang (client died without FIN) may not be noticed until the proxy/OS timeout; that is the case the per-key duration cap exists to bound.

### Messages the server sends

| `type` | Shape | When |
|---|---|---|
| `ready` | `{}` | Auth succeeded, safe to start sending audio |
| `copilot_unavailable` | `{}` | No LLM provider connected — live suggestions off, transcription still works |
| `transcript` | `{ segment: {id, channel, start_ms, end_ms, text} }` | A committed (final) transcript segment |
| `partial_transcript` | `{ channel, text }` | Disposable live preview — replaced by the next `transcript` / `partial_transcript` for that channel, **never persisted** |
| `copilot` | `{ suggestion, blockers: [string], action_items: [string], coach_score }` | One live-coaching cycle. Also persisted (minus `action_items`) as a row `GET /insights` returns |
| `diarization_update` / `speaker_hint` | `{ is_snapshot, removed_segment_ids: [string], segments: [{id, channel, start_ms, end_ms, text, speaker_label, linked_user_id}] }` | Speaker labels resolving/changing. A `speaker_hint` is a fast guess; a later `diarization_update` for the same segment overwrites it. Apply as a diff: drop `removed_segment_ids`, upsert `segments` |
| `stopped` | `{}` | Acknowledges a graceful client-sent `stop` only |
| `debug_event` | `{ stage, at_ms, detail }` | Admin-only, and only after the client sent `{"type":"debug","enabled":true}`. Non-admins' debug frames are ignored |

Client → server text frames besides `auth` / `stop`: `{"type":"debug","enabled": true|false}` (admin no-op otherwise).

---

## Pre/post call-type hooks

Configured per call type in **Admin → Call types** (JWT / admin UI — not the API-key surface). Corella is the HTTP *client* here; your system is the server.

These are independent of API keys. They fire for browser-recorded meetings of that type too.

### Before the call (pre-call)

Fires synchronously from `POST /api/meetings`, before the response comes back — bounded by `pre_call_timeout_seconds` (default **5s**).

- **Method**: any (`GET` by default).
- **URL / headers / body**: admin-configurable. Headers may include an encrypted `Authorization`-style secret (write-only; never returned by the admin API).
- **Body template**: `{{placeholder}}` substitution for `POST`/`PUT`/`PATCH`. Only meeting-level fields exist yet: `{{meeting_id}}`, `{{owner_id}}`, `{{owner_name}}`, `{{title}}`, `{{call_type}}`, `{{status}}`, `{{created_at}}`. Transcript/report placeholders are post-call only. A failed template render aborts that pre-call (logged, swallowed) — meeting creation still succeeds.
- **"Use response as conversation context"**: when on, the response body is stored on the meeting (`pre_call_context`) and fed into every live-copilot cycle, *alongside* (not instead of) the group's knowledge base. Capped at `pre_call_context_max_chars` (default 20,000). Independent of whether a body template is set — the request still fires; this flag only controls whether the *response* becomes context. With it off, a pre-call is still useful as a side effect (notify another system a call started).
- Non-2xx, timeout, DNS failure, bad URL: logged and swallowed. Returns no context. **Never fails meeting creation.**

### After the call (post-call)

Fires **once**, right after a call's report finishes **auto**-generating (the Celery `generate_report` task). Not from `POST /api/meetings/{id}/report`. Not if auto-report is skipped (no LLM provider, or `ReportError`). Timeout `post_call_timeout_seconds` (default **30s**). Failures are logged and swallowed — they never mark the meeting failed.

- **Method / URL / headers**: same shape as pre-call (defaults to `POST`).
- **Body**: either a hand-written JSON template with `{{placeholder}}` tokens, or — with **"Send everything"** on — the full structured payload below, no template needed. Send-everything **wins** over a template (the template is not also applied).
- Post-call always starts from `Content-Type: application/json`, then merges custom headers, then applies the three mandatory headers. A custom header can override `Content-Type`; it cannot override the `X-Corella-*` names.

If auto-report is skipped, the post-call never runs even if the hook is configured. The meeting can still be `ready` with empty report fields.

### The three mandatory headers

On **every** pre-call and post-call, **always**, after custom headers are merged. A custom header of the same name is overwritten:

| Header | Value |
|---|---|
| `X-Corella-App-Url` | This instance's public URL (`public_app_url`, default `http://localhost:8080`) — identifies *which* deployment the request came from. Not the same as `PUBLIC_API_URL` (that's which API the browser talks to). |
| `X-Corella-Meeting-Id` | The meeting's UUID |
| `X-Corella-User-Id` | The meeting **owner's** user UUID (not whoever triggered create, which is the same person when using their own API key) |

### Placeholder reference (post-call body templates)

| Placeholder | Value |
|---|---|
| `{{meeting_id}}`, `{{owner_id}}`, `{{owner_name}}` | Identity |
| `{{title}}`, `{{call_type}}`, `{{status}}` | Basics (`call_type` is the type's **name** string, or JSON `null` if untyped) |
| `{{summary}}`, `{{key_topics}}`, `{{sentiment}}`, `{{notable_quotes}}` | Report content |
| `{{coach_score}}`, `{{estimated_cost_usd}}`, `{{talk_ratio}}` | Report metrics (`talk_ratio` is `{"me": <pct>, "them": <pct>}`) |
| `{{action_items}}` | `[{"text": "...", "status": "open"\|"done"}, ...]` |
| `{{copilot_insights}}` | `[{"at_ms": 12000, "suggestion": "...", "blockers": [...], "coach_score": 74}, ...]` — same timeline `GET /insights` returns |
| `{{transcript}}` | Full transcript, `"Me: ...\nThem: ...\n..."` (channel-based labels, not resolved speaker names) |
| `{{created_at}}`, `{{started_at}}`, `{{ended_at}}`, `{{duration_seconds}}` | Timing (ISO-8601 or JSON `null`) |
| `{{full_payload}}` | The entire structured payload below, as one embedded JSON object — equivalent to turning "Send everything" on, but usable inline in a hand-written template |

A placeholder inside a quoted string (`"summary": "{{summary}}"`) is substituted as a properly JSON-escaped string (quotes/newlines in a summary cannot break the surrounding JSON). An array/object/number/null placeholder (`"key_topics": {{key_topics}}`) must sit *outside* quotes — it substitutes as real JSON. `{{full_payload}}` is an object, so it belongs outside quotes.

Pre-call templates use the same escaping rules on their smaller set.

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

`title` here is the **report** title (the LLM may rewrite it), not necessarily the create-time title.

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

From inside the `api`/`worker` **containers**, `localhost` is the container itself. Point hooks at the host with `http://host.docker.internal:<port>/...`.

---

## Errors

REST errors are standard FastAPI/Pydantic shape: `{"detail": "..."}` with the appropriate 4xx/5xx (`401` invalid/missing credentials, `403` insufficient role — admin routes, `404` not found or not owned, `409` conflict, `415` upload that doesn't look like audio, `422` validation / unknown `call_type_id` / no LLM for `POST /report`).

WebSocket errors are close codes only (`4401` / `4404` / `4409` / `4410`) plus a reason string. There is no in-band `{"type":"error",...}` today; a failed connection is always a closed connection.

---

## Quirks (do not skip)

These are the ones that bite integrations. All are real behavior, not omissions.

1. **API keys are not a second copy of the whole API.** List/search/upload/delete/settings/admin/call-type listing are JWT-only. Plan around that: stash `call_type_id` yourself, or always use the default type.
2. **Create ≠ "recorded via API".** The Dashboard badge is set when the **WebSocket** authenticates with a key, not at `POST /api/meetings`.
3. **Disconnect finalizes. You cannot resume.** Tab close, process kill, and `stop` all end the meeting. Reconnect → `4409`. Create a new one.
4. **One live connection per meeting.** Second socket → `4409`, first keeps going. Corella's own "Go to live session" is hidden on API-recorded meetings for this reason; the lock is the real backstop.
5. **Duration cap is API-key / WebSocket only.** Forgotten Corella browser tabs are not capped. REST polling is not capped. Changing the key's minutes does not affect an in-flight session.
6. **`4410` still produces a real meeting** (audio, report, post-call). Handle it as "time's up", not "auth failed".
7. **Auth is the first WS frame, in 5 seconds, or `4401`.** Don't open the socket and then wait on your audio pipeline before sending `auth`.
8. **Audio format is fixed.** PCM16LE mono 16 kHz, channel byte prefix. No `Content-Type`, no JSON wrapper, no Opus/WebM.
9. **Partial transcripts are UI-only.** Only `transcript` (and later diarization rewrites of those segments) is persisted.
10. **Live `copilot.action_items` ≠ report action items.** The WS field is ephemeral per cycle; durable open/done items come from the report / post-call payload. `GET /insights` has suggestion/blockers/score, not those live action items.
11. **Post-call runs only after a successful auto-report.** No LLM connected → no auto-report → no hook, even if the recording finalized to `ready`. Manual `POST /report` also does not fire it.
12. **Pre-call failure is silent to the caller.** You still get `201`. Check Corella logs (or your receiving endpoint) if you expected context/side effects.
13. **Mandatory hook headers always win.** You cannot spoof `X-Corella-Meeting-Id` via custom header config.
14. **`GET /meetings/{id}` is group-visible; transcript is not.** An API key whose owner shares a group can read a colleague's summary, not their transcript.
15. **CORS applies to browser REST, not to server-to-server REST.** Machine callers should not send the request from a random web origin unless that origin is in `CORS_ORIGINS`.
16. **Key plaintext is unrecoverable.** Settings list shows a prefix (`sk_live_xxxxxx…`) only. Rotate by creating a new key and deleting the old one; in-flight sockets using the deleted key fail on the next auth (the current socket is not torn down by delete — revoke is "cannot authenticate again").
17. **Half-open TCP is why the duration cap exists.** Disconnect-to-finalize only runs when the server *sees* the socket die.

---

## Testing your integration: `server/scripts/api_test_server.py`

A small FastAPI app, committed in this repo, that stands in for "the other system" on the receiving end of a pre/post hook — and also serves a minimal live-recording UI built from this document.

Run it:

```
cd server && .venv/bin/uvicorn scripts.api_test_server:app --port 9199 --reload
```

### Hooks

From inside the `api`/`worker` containers use `host.docker.internal`, not `localhost`:

```
http://host.docker.internal:9199/pre     # any path containing "pre"  → 200, a fake CRM-lookup-shaped text body
http://host.docker.internal:9199/post    # anything else             → 200, {"ok": true}
http://host.docker.internal:9199/anyfail # any path containing "fail" → 500, for testing graceful failure
http://host.docker.internal:9199/anyslow # any path containing "slow" → sleeps 8s, for testing the timeout path
```

Every request is logged (method, the three mandatory headers checked off, body) and kept in memory — `GET http://localhost:9199/requests` to inspect, `DELETE` to clear.

`server/tests/test_call_hooks_integration.py` starts this exact app as a subprocess and asserts against real loopback HTTP — both hooks in "special" (`pre_call_use_as_context` / `post_call_send_full_payload`) and "regular" (plain fetch / custom template) modes, plus failure and timeout — on top of `test_call_hooks.py`'s monkeypatched-`httpx` unit tests.

### Live-recording WebSocket UI

`http://localhost:9199/` — create a meeting with an API key, open the live WebSocket, stream the mic, render transcript / speaker labels / copilot. Meeting create is proxied through this server (`POST /start-test-call`) so you don't have to add this origin to the real app's `CORS_ORIGINS`; the WebSocket is opened browser-to-API directly.

1. Create an API key (Settings → API keys) and paste it in, with the API base URL (`http://localhost:8090` in local Docker).
2. **Start** — `POST /api/meetings`, then WS auth with that key, then mic capture (same PCM16/16 kHz worklet as the real app, served at `/pcm-worklet.js`).
3. Speak — transcript, labels, and (if an LLM is connected) coaching update live.
4. **Stop** — finalizes like a real recording (auto-report, post-call hook if configured).

This is a second, independent implementation of the protocol `web/src/lib/live.ts` implements — proving this document is enough to build a working live client. Confirmed end-to-end with a real microphone: live transcript, speaker labels, and coaching updating in real time, and a clean stop finalizing with a real report.
