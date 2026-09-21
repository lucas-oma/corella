# Corella API

How to integrate an external system with Corella. There are **three** integration types — they compose, they are not alternatives:

1. **Inbound REST (API keys)** — create and read meetings as a specific user, without a browser login.
2. **Live WebSocket** — stream a recording in real time. Same protocol the browser app uses; an API key authenticates it.
3. **Outbound call-type hooks** — Corella calls *your* HTTP endpoint before a call of a given type starts and/or after it finishes.

Interactive request/response schemas for every HTTP route (including browser-only ones) live at `/docs` (FastAPI's Swagger UI). This document is the integration contract: which credentials work where, the WebSocket protocol, hook payloads, and every quirk that the schema cannot show.

Base URL is your instance's API origin. In local Docker that is `http://localhost:8090` by default (`API_PORT` in `.env`; host-side remap in `docker-compose.yml` — the container itself still listens on 8000). Unauthenticated liveness: `GET /api/health` → `{"status":"ok"}`.

---

## The three types at a glance

| | Direction | Auth | When |
|---|---|---|---|
| **API key REST** | You → Corella | `Authorization: Bearer sk_live_…` | Create a meeting, poll results |
| **Live WebSocket** | You ↔ Corella | First text frame `{"type":"auth","api_key":"…"}` | Stream audio, receive transcript/copilot live |
| **Call-type hooks** | Corella → you | *Your* endpoint; Corella always sends four `X-Corella-*` headers | Pre-call lookup / post-call push |

A typical phone-bridge or CRM integration uses all three: REST to create the meeting, WebSocket to stream audio, a post-call hook to push the finished report back into the CRM. A "just notify us when a call ends" integration only needs the hook. A "pull the transcript later" integration only needs the API key.

---

## Authentication

Two credential types exist. They are **not** interchangeable on every route.

### JWT (browser login)

`POST /api/auth/login` returns a short-lived JWT (`access_token_expire_minutes`, default 24h, HS256, payload `{sub, exp}` — org is **not** in the token). This is what the web app uses. It unlocks **the whole API** (settings, organization, super-admin, knowledge base, uploads, deletes, …). Active org is stored on the user (`PUT /api/organizations/current`).

`GET /api/auth/me` returns `is_super_admin`, `active_organization_id`, `organizations: [{id, name, role, is_instance_org}]`, and `group_ids` in the active org. There is no global `role` / `group_id`.

`GET /api/auth/config` is public: `{ "allow_public_registration": true, "max_orgs_per_user": 1, "email_invites": false }`. `email_invites` is true when this instance has both `RESEND_API_KEY` and `RESEND_FROM_EMAIL` set.

### API key (machine / external)

A long-lived credential created in **Settings → API keys** (or `POST /api/settings/api-keys` — that management surface itself still requires a JWT). Looks like `sk_live_<random>`. Shown in full **exactly once**, at creation — only a sha256 hash is stored, so a lost key cannot be recovered; create a new one.

```
Authorization: Bearer sk_live_<your-key>
```

An API key acts **as the user who created it, in the organization it was minted in**. Switching orgs in the UI does not move the key. No fine-grained scopes — treat it like a password for that account in that org. It can only reach the **integration surface** below, not org/super-admin surfaces. Using it on a JWT-only route returns `401`.

The `sk_live_` prefix is how the server tells a key apart from a JWT **without** attempting a JWT decode first. Do not strip it; a key that does not start with `sk_live_` is treated as a (then-invalid) JWT.

`last_used_at` on the key is bumped on every successful REST or WebSocket authentication.

### Where an API key is accepted

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/call-types` | Lightweight list: `{id, name, slug, is_default}` — pick an id for create |
| `POST` | `/api/meetings` | Create (and start) a meeting. Optional `call_type_id`; omit/`null` = that org's default |
| `GET` | `/api/meetings/{id}` | Meeting + report fields. **Group-visible** — see [Access](#access-what-the-key-can-see) |
| `GET` | `/api/meetings/{id}/transcript` | Owner, org owner/admin, or super_admin |
| `GET` | `/api/meetings/{id}/insights` | Owner, org owner/admin, or super_admin |
| `POST` | `/api/meetings/{id}/report` | Owner only; synchronous regenerate |
| WS | `/ws/meetings/{id}/live` | Live recording |

Everything else is JWT-only. In particular an API key **cannot**:

- List meetings (`GET /api/meetings`), search, group/all lists
- Upload / download audio, delete a meeting, read or toggle action items
- Manage API keys, providers, preferences, knowledge base, or org/super-admin resources

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

{ "title": "Sales call with Acme", "call_type_id": null, "capture_mode": "open_mic", "capture_app": null }
```

- `title` defaults to `"Untitled meeting"` if omitted.
- `call_type_id` is optional. Omit it or send `null` to use whichever call type is currently marked default. Unknown id → `422`. If the instance has no call types at all, the meeting is created untyped (`call_type: null`) and no pre-call fires. `GET /api/call-types` (API key or JWT) returns `{id, name, slug, is_default}` so you can pick an id.
- `capture_mode` is how audio arrives, not the call type. `open_mic` (default) is one microphone. `meeting_tab` is mic plus a shared tab (Meet/Teams/Zoom). `upload` is set automatically when a file is posted to `/audio`. `capture_app` is `meet` | `teams` | `zoom` | `other` | `null` and is stored only for `meeting_tab`. First PCM on the them channel also promotes `open_mic` → `meeting_tab`.
- There is **no separate "start" call**. The meeting is created with `status: "recording"` immediately.
- If that call type has a pre-call hook, it fires **here**. Default is **sync**: Corella waits (bounded by `pre_call_timeout_seconds`, default 5s) before the `201` comes back. Admins can mark the hook **async** — create returns immediately and the worker runs the same request; if "use as context" is on, live copilot picks the body up on the next cycle. A slow/broken hook never fails creation; you just get no extra context that time.
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
  "api_key_name": null, "capture_mode": "open_mic", "capture_app": null
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

**Transcript.** `channel` is `"me"` | `"them"` | `"unknown"` — the audio pipe, not the printed name. `speaker_label` may still be `null` while same-room diarization catches up (or permanently, for a voice that never accumulated enough speech). `linked_user_id` is set only when the label resolved to an enrolled account — render `"Me"` only when it equals the viewing user's id. `{{corella.transcript}}` / the report prompt use those identities (`Me` / `Speaker N` / a name), not the raw channel.

**Insights.** The persisted live-copilot timeline, timestamp-ordered. `at_ms` lines up with `transcript[].start_ms` / `end_ms`. This shape does **not** include `action_items` — those on the live `copilot` WebSocket message are per-cycle suggestions; the durable open/done action items live on the report (`POST /report` / post-call payload).

**`POST /report`.** Owner-only (not even an org admin of someone else's meeting). Requires an LLM provider connected for that account (`422` otherwise). Synchronous. A normal auto-generated report already fires once a recording finishes; this is for a forced refresh. **It does not fire the post-call hook** — regenerating is not "the conversation ending" a second time.

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

Same rules as the browser, because the key *is* that user **in the key's org**.

| Resource | Meeting owner | Org owner/admin | Group-mate | Member (no shared group) | Super admin |
|---|---|---|---|---|---|
| `GET /meetings/{id}` (report fields) | yes | yes (this org) | yes (this org) | no | yes (cross-org) |
| Transcript / insights / audio | yes | yes (this org) | **no** | no | yes (cross-org) |
| `POST /report`, delete, action-item writes | yes | **no** | no | no | **no** |
| Live WebSocket | yes | no | no | no | no (must be the owner) |
| `GET /api/meetings/org` and `/search/org` | — | yes (this org) | — | — | — |
| `GET /api/meetings/all` and `/search/all` | — | **no** (not the dashboard All tab) | — | — | yes (instance-wide) |

Group membership never grants the raw recording. `404` is used for both missing and forbidden on meeting reads (no `403` there), so you cannot probe whether an id exists. Org-admin "All" on the dashboard hits `/meetings/org`, not `/meetings/all`.

---

## Organizations and invites (JWT)

Browser-only. Active org is stored on the user, not in the JWT.

| Method | Path | Who |
|---|---|---|
| `GET` | `/api/organizations` | Any signed-in user — their memberships |
| `POST` | `/api/organizations` | Open mode, under `MAX_ORGS_PER_USER` owned-org cap |
| `PUT` | `/api/organizations/current` | Member of the target org |
| `PATCH` / `DELETE` | `/api/organizations/{id}` | Owner/admin rename; owner delete (not the instance/default org, and never the last org) |
| `GET` / `POST` / `PATCH` / `DELETE` | `/api/organizations/{id}/members` | Owner/admin; cannot touch the owner; cannot invite/create as `owner` |
| `POST` | `/api/organizations/{id}/transfer` | Owner only |
| `POST` | `/api/organizations/{id}/leave` | Anyone except the last owner |
| `GET` / `POST` / `DELETE` | `/api/organizations/{id}/invites` | Owner/admin. Token returned once; store a hash. Resend mints a new token. Create/resend email the link when Resend is configured (`email_sent` on that response); a send failure does not fail the invite. |
| `GET` | `/api/invites/{token}` | Public preview (org name, email, role, expiry) |
| `POST` | `/api/invites/{token}/accept` | Public — works when registration is closed. New email: body `{password, full_name}`. Existing email: log in first (409 if logged out, 403 if email mismatch) |
| `GET` / `POST` / `DELETE` / `PUT` | `/api/organizations/{id}/groups` (+ `/members`) | Owner/admin. Groups are org-scoped; membership is many-to-many |

Call types, secrets, and org costs stay on `/api/admin/*` but are scoped to the **active org** (org owner/admin). Super-admin-only: `GET /api/admin/organizations`, `GET /api/admin/users`, `PATCH /api/admin/users/{id}/super-admin`, `GET /api/admin/costs/instance`, plus `/api/meetings/all` and `/search/all`.

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
| `transcript` | `{ segment: {id, channel, start_ms, end_ms, text} }` | A committed (final) transcript segment. **No `speaker_label`** — see quirk 18 |
| `partial_transcript` | `{ channel, text }` | Disposable live preview — replaced by the next `transcript` / `partial_transcript` for that channel, **never persisted** |
| `copilot` | `{ suggestion, blockers: [string], action_items: [string], coach_score }` | One live-coaching cycle. Also persisted (minus `action_items`) as a row `GET /insights` returns |
| `diarization_update` / `speaker_hint` | `{ is_snapshot, removed_segment_ids: [string], segments: [{id, channel, start_ms, end_ms, text, speaker_label, linked_user_id}] }` | Speaker labels resolving/changing. A `speaker_hint` is a fast guess; a later `diarization_update` for the same segment overwrites it. Apply as a diff: drop `removed_segment_ids`, upsert `segments`. Keep names in a **separate map keyed by segment id** — do not store them only on the transcript object |
| `stopped` | `{}` | Acknowledges a graceful client-sent `stop` only |
| `debug_event` | `{ stage, at_ms, detail }` | Org owner/admin or super_admin only, and only after the client sent `{"type":"debug","enabled":true}`. Other users' debug frames are ignored |

Client → server text frames besides `auth` / `stop`: `{"type":"debug","enabled": true|false}` (no-op otherwise).

---

## Pre/post call-type hooks

Configured per call type in **Organization → Call types** (JWT / org-admin UI — not the API-key surface). Corella is the HTTP *client* here; your system is the server.

These are independent of API keys. They fire for browser-recorded meetings of that type too.

### Before the call (pre-call)

Default: fires **synchronously** from `POST /api/meetings`, before the response comes back — bounded by `pre_call_timeout_seconds` (default **5s**). Org owner/admins can check **Don't wait (async)** on the call type: create returns immediately, `corella.dispatch_pre_call` runs the same request, and context (if enabled) lands when the worker finishes.

- **Method**: any (`GET` by default).
- **URL / headers / body**: org owner/admin-configurable. Headers are a JSON object. Put tokens in **Organization → Secrets** and reference them as `{{secret.NAME}}` (e.g. `{"X-Corella-Webhook-Secret": "{{secret.WEBHOOK_SECRET}}"}`). Org-admin GET returns that template — never the resolved value. Dispatch interpolates the secret at send time. A missing/unknown secret aborts that hook (logged, swallowed). Literal header values still work but will be visible to org admins on the next GET.
- **Body template**: `{{corella.KEY}}` substitution for `POST`/`PUT`/`PATCH`. Only meeting-level fields exist yet: `{{corella.meeting_id}}`, `{{corella.owner_id}}`, `{{corella.owner_name}}`, `{{corella.title}}`, `{{corella.call_type}}`, `{{corella.capture_mode}}`, `{{corella.capture_app}}`, `{{corella.status}}`, `{{corella.created_at}}`. Transcript/report placeholders are post-call only. A failed template render aborts that pre-call (logged, swallowed) — meeting creation still succeeds.
- **"Use response as conversation context"**: when on, the response body is stored on the meeting (`pre_call_context`) and fed into every live-copilot cycle, *alongside* (not instead of) the group's knowledge base. Capped at `pre_call_context_max_chars` (default 20,000). Independent of whether a body template is set — the request still fires; this flag only controls whether the *response* becomes context. With it off, a pre-call is still useful as a side effect (notify another system a call started).
- **Don't wait (async)** (default off): queue the request instead of blocking create. Use this when the lookup is slow and you would rather start recording first.
- Non-2xx, timeout, DNS failure, bad URL: logged and swallowed. Returns no context. **Never fails meeting creation.**

### After the call (post-call)

Fires **once**, right after a call's report finishes **auto**-generating (the Celery `generate_report` task). Not from `POST /api/meetings/{id}/report`. Not if auto-report is skipped (no LLM provider, or `ReportError`). Timeout `post_call_timeout_seconds` (default **30s**). Failures are logged and swallowed — they never mark the meeting failed. **Don't wait (async)** (default off) queues `corella.dispatch_post_call` so the report worker does not sit on that timeout.

- **Method / URL / headers**: same shape as pre-call (defaults to `POST`).
- **Body**: either a hand-written JSON template with `{{placeholder}}` tokens, or — with **"Send everything"** on — the full structured payload below, no template needed. Send-everything **wins** over a template (the template is not also applied).
- Post-call always starts from `Content-Type: application/json`, then merges custom headers, then applies the four mandatory headers. A custom header can override `Content-Type`; it cannot override the `X-Corella-*` names.

If auto-report is skipped, the post-call never runs even if the hook is configured. The meeting can still be `ready` with empty report fields.

Org owner/admins can open a meeting and expand **API logs** to see each pre/post attempt (method, URL, status, redacted headers/body). Regular members never see this. `GET /api/meetings/{id}/hook-logs` is org-admin (or super_admin) only (403 otherwise). Credential-named headers (`Authorization`, `*secret*`, `*token*`, …) and resolved `{{secret.NAME}}` values are stored as `••••`.

### The four mandatory headers

On **every** pre-call and post-call, **always**, after custom headers are merged. A custom header of the same name is overwritten:

| Header | Value |
|---|---|
| `X-Corella-App-Url` | This instance's public URL (`public_app_url`, default `http://localhost:8080` / `WEB_PORT`) — identifies *which* deployment the request came from. Not the same as `PUBLIC_API_URL` (that's which API the browser talks to). |
| `X-Corella-Meeting-Id` | The meeting's UUID |
| `X-Corella-User-Id` | The meeting **owner's** user UUID (not whoever triggered create, which is the same person when using their own API key) |
| `X-Corella-Org-Id` | The meeting's organization UUID |

### Placeholder reference (post-call body templates)

Body tokens are `{{corella.KEY}}` only — unprefixed `{{KEY}}` is left as-is. Organization secrets (`{{secret.NAME}}`) are headers-only.

| Placeholder | Value |
|---|---|
| `{{corella.meeting_id}}`, `{{corella.owner_id}}`, `{{corella.owner_name}}` | Identity |
| `{{corella.title}}`, `{{corella.call_type}}`, `{{corella.status}}` | Basics (`call_type` is the type's **name** string, or JSON `null` if untyped) |
| `{{corella.capture_mode}}`, `{{corella.capture_app}}` | How audio arrived: `open_mic` / `meeting_tab` / `upload`, and `meet` / `teams` / `zoom` / `other` / JSON `null` |
| `{{corella.summary}}`, `{{corella.key_topics}}`, `{{corella.sentiment}}`, `{{corella.notable_quotes}}` | Report content |
| `{{corella.coach_score}}`, `{{corella.estimated_cost_usd}}`, `{{corella.talk_ratio}}` | Report metrics (`talk_ratio` is `{"me": <pct>, "them": <pct>}` on `meeting_tab` only; JSON `null` otherwise) |
| `{{corella.speaker_share}}` | Open-mic / upload talk share by display name: `[{"label": "Speaker 1", "pct": 40}, …]` (sums to 100). JSON `null` on `meeting_tab` |
| `{{corella.action_items}}` | Report digest only (`source=report`): `[{"text": "...", "status": "open"\|"done"}, ...]` — not the live-capture pile |
| `{{corella.copilot_insights}}` | `[{"at_ms": 12000, "suggestion": "...", "blockers": [...], "coach_score": 74}, ...]` — same timeline `GET /insights` returns |
| `{{corella.transcript}}` | Full transcript as `"Me: ...\nSpeaker 1: ..."` — owner's enrolled voice (or unlabeled `meeting_tab` mic) is `Me`; other people are names or `Speaker N`. Not the raw me/them channel. |
| `{{corella.created_at}}`, `{{corella.started_at}}`, `{{corella.ended_at}}`, `{{corella.duration_seconds}}` | Timing (ISO-8601 or JSON `null`) |
| `{{corella.full_payload}}` | The entire structured payload below, as one embedded JSON object — equivalent to turning "Send everything" on, but usable inline in a hand-written template |

A placeholder inside a quoted string (`"summary": "{{corella.summary}}"`) is substituted as a properly JSON-escaped string (quotes/newlines in a summary cannot break the surrounding JSON). An array/object/number/null placeholder (`"key_topics": {{corella.key_topics}}`) must sit *outside* quotes — it substitutes as real JSON. `{{corella.full_payload}}` is an object, so it belongs outside quotes.

Pre-call templates use the same escaping rules on their smaller set.

### The full payload shape ("Send everything" / `{{corella.full_payload}}`)

```json
{
  "meeting_id": "3fa2...", "owner_id": "...", "owner_name": "Jane Doe",
  "title": "Sales call with Acme", "call_type": "Sales",
  "capture_mode": "open_mic", "capture_app": null, "status": "ready",
  "summary": "...", "key_topics": ["Pricing", "Timeline"], "sentiment": "Positive",
  "notable_quotes": ["..."],
  "coach_score": 82, "estimated_cost_usd": 0.0341, "talk_ratio": null,
  "speaker_share": [{"label": "Me", "pct": 55}, {"label": "Speaker 1", "pct": 45}],
  "action_items": [{"text": "Send proposal by Friday", "status": "open"}],
  "copilot_insights": [
    {"at_ms": 42000, "suggestion": "Address the pricing objection directly", "blockers": ["Pricing concern raised"], "coach_score": 71}
  ],
  "transcript": "Me: ...\nSpeaker 1: ...",
  "created_at": "2026-09-08T12:00:00Z", "started_at": "2026-09-08T12:00:05Z",
  "ended_at": "2026-09-08T12:31:20Z", "duration_seconds": 1875
}
```

`title` here is the **report** title (the LLM may rewrite it), not necessarily the create-time title. `action_items` is the post-call digest (`source=report`), not the live-copilot capture list.

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

Corella calls `GET https://your-crm.example.com/lookup?...` with the four mandatory headers attached; your endpoint returns plain text or JSON, which becomes the "External context" block in every live-coaching prompt for that call if "use as context" is on.

From inside the `api`/`worker` **containers**, `localhost` is the container itself. Point hooks at a process on the host with `http://host.docker.internal:<port>/...` (compose sets `extra_hosts: host-gateway` so this resolves on Linux too). In production, use a **public** URL the container can DNS-resolve (`https://<project>.supabase.co/functions/v1/...`), not localhost and not a hostname that only exists on your laptop. A failed DNS lookup (`Name or service not known`) still returns `201` — the meeting is created, the hook is not.

---

## Errors

REST errors are standard FastAPI/Pydantic shape: `{"detail": "..."}` with the appropriate 4xx/5xx (`401` invalid/missing credentials, `403` insufficient role — org-admin / super-admin routes, `404` not found or not owned, `409` conflict, `410` expired invite, `415` upload that doesn't look like audio, `422` validation / unknown `call_type_id` / no LLM for `POST /report`).

WebSocket errors are close codes only (`4401` / `4404` / `4409` / `4410`) plus a reason string. There is no in-band `{"type":"error",...}` today; a failed connection is always a closed connection.

---

## Quirks (do not skip)

These are the ones that bite integrations. All are real behavior, not omissions.

1. **API keys are not a second copy of the whole API.** List/search/upload/delete/settings/org/super-admin (including *managing* call types) are JWT-only. `GET /api/call-types` is the exception — keys can list `{id, name, slug, is_default}` for the key's org so create can send a real `call_type_id`.
2. **API keys stay in the org they were created in.** The owner's switcher does not move them.
3. **Create ≠ the "API: …" badge.** The Dashboard badge is set when the **WebSocket** authenticates with a key, not at `POST /api/meetings`.
4. **Disconnect finalizes. You cannot resume.** Tab close, process kill, and `stop` all end the meeting. Reconnect → `4409`. Create a new one.
5. **One live connection per meeting.** Second socket → `4409`, first keeps going. Corella's own "Go to live session" is hidden on API-recorded meetings for this reason; the lock is the real backstop.
6. **Duration cap is API-key / WebSocket only.** Forgotten Corella browser tabs are not capped. REST polling is not capped. Changing the key's minutes does not affect an in-flight session.
7. **`4410` still produces a real meeting** (audio, report, post-call). Handle it as "time's up", not "auth failed".
8. **Auth is the first WS frame, in 5 seconds, or `4401`.** Don't open the socket and then wait on your audio pipeline before sending `auth`.
9. **Audio format is fixed.** PCM16LE mono 16 kHz, channel byte prefix. No `Content-Type`, no JSON wrapper, no Opus/WebM.
10. **Partial transcripts are UI-only.** Only `transcript` (and later diarization rewrites of those segments) is persisted.
11. **Live `copilot.action_items` ≠ report action items.** The WS field is ephemeral per cycle. Durable items are `GET /action-items` with `source=live|report`. The report / post-call payload is the digest (`source=report`) only. `GET /insights` has suggestion/blockers/score, not those live action items.
12. **Post-call runs only after a successful auto-report.** No LLM connected → no auto-report → no hook, even if the recording finalized to `ready`. Manual `POST /report` also does not fire it.
13. **Pre-call failure is silent to the caller.** You still get `201`. The most common prod miss is DNS: the hostname in the hook URL does not resolve *inside* the api container (`Name or service not known`). Org owner/admins can open the meeting's **API logs**; otherwise check Corella logs or your receiving endpoint.
14. **Mandatory hook headers always win.** You cannot spoof `X-Corella-Meeting-Id` or `X-Corella-Org-Id` via custom header config.
15. **`GET /meetings/{id}` is group-visible; transcript is not.** An API key whose owner shares a group in that org can read a colleague's summary, not their transcript.
16. **Org "All" is not instance `/all`.** Org owner/admin dashboard All uses `GET /api/meetings/org`. `GET /api/meetings/all` and `/search/all` are super-admin-only and the only cross-org read path.
17. **CORS applies to browser REST, not to server-to-server REST.** Machine callers should not send the request from a random web origin unless that origin is in `CORS_ORIGINS`.
18. **Key plaintext is unrecoverable.** Settings list shows a prefix (`sk_live_xxxxxx…`) only. Rotate by creating a new key and deleting the old one; in-flight sockets using the deleted key fail on the next auth (the current socket is not torn down by delete — revoke is "cannot authenticate again").
19. **Half-open TCP is why the duration cap exists.** Disconnect-to-finalize only runs when the server *sees* the socket die.
20. **`transcript` has no `speaker_label`, and it often arrives *after* `diarization_update` for the same id** (Deepgram publishes the label to Redis before sending the transcript frame). If you store the name on the transcript object and then `set(id, transcriptEvent)`, you wipe every split and every line falls back to `me`. Keep names in a separate map keyed by segment id — that is what Corella's own live UI does, what the `api_test_server` page at `/` does, and what [`packages/corella-live`](packages/corella-live) does for you.

---

## Website client: `packages/corella-live`

An installable TypeScript client that does what the test page below does: create a meeting with an API key, open the live WebSocket, stream PCM, and emit a speaker-labeled transcript. `npm install ./packages/corella-live` from this repo (not on the public registry yet). See that package's README for a 15-line browser example. CORS still applies to `POST /api/meetings` from a browser origin — create from your server and `connect({ meetingId })` if you cannot add the origin to `CORS_ORIGINS`.

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

Every request is logged (method, the four mandatory headers checked off, body) and kept in memory — `GET http://localhost:9199/requests` to inspect, `DELETE` to clear.

`server/tests/test_call_hooks_integration.py` starts this exact app as a subprocess and asserts against real loopback HTTP — both hooks in "special" (`pre_call_use_as_context` / `post_call_send_full_payload`) and "regular" (plain fetch / custom template) modes, plus failure and timeout — on top of `test_call_hooks.py`'s monkeypatched-`httpx` unit tests.

### Live-recording WebSocket UI

`http://localhost:9199/` — a sample site that uses [`packages/corella-live`](packages/corella-live) (served at `/corella-live/*`). This page is only UI: create is still proxied (`POST /start-test-call`) so you don't have to add this origin to `CORS_ORIGINS`; connect + mic + labeled transcript go through the package.

Build the client once first:

```
cd packages/corella-live && npm install && npm run build
```

1. Create an API key (Settings → API keys) and paste it in, with the API base URL (`http://localhost:8090` in local Docker, or whatever `API_PORT` you set).
2. **Start** — proxied `POST /api/meetings`, then `CorellaLive.connect` + `startMic()`.
3. Speak — transcript (already labeled) and (if an LLM is connected) coaching update live.
4. **Stop** — finalizes like a real recording (auto-report, post-call hook if configured).
