# corella-live

Browser client for [Corella](https://github.com/lucas-oma/corella)’s live recording API. Stream a microphone (or raw PCM) and get a speaker-labeled transcript plus live copilot: suggestion, blockers, action items, coach score, sentiment, and talk share.

It handles the WebSocket quirks for you — speaker names live **off** the transcript row so a later unlabeled `transcript` frame cannot wipe Deepgram splits.

## Install

```bash
npm install corella-live
```

From this repo instead of npm: `npm install ./packages/corella-live`.

Requires a Corella instance, an API key (`Settings → API keys`, `sk_live_…`), and a runtime with `fetch` + `WebSocket` (a browser, typically).

## Quick start

```ts
import { CorellaLive, CLOSE } from "corella-live";

const session = await CorellaLive.start({
  apiBase: "https://corella.example.com",
  apiKey: "sk_live_…",
  title: "Support call",
});

session.onTranscript((lines) => {
  // lines[i].speakerLabel, .text, .start_ms, .end_ms — already merged
});
session.onPartial((p) => {
  // rolling draft per channel until the next committed line
  showDraft(p.me, p.them);
});
session.onCopilot((c) => {
  // c.coach_score        number | null   (0–100)
  // c.sentiment          closed set or null  (see below)
  // c.speaker_share      [{ label, pct }, …] | null
  // c.suggestion, c.blockers, c.action_items
});
session.onCopilotUnavailable(() => {
  // this account has no LLM — no copilot frames will arrive
});
session.onClose(({ code, reason }) => {
  if (code === CLOSE.DURATION_LIMIT) {
    // 4410 — this key’s max duration; the meeting still finalizes
  }
});

await session.startMic(); // channel "me"
session.stop();           // or session.close() — both finalize
```

`createMeeting({ apiBase, apiKey, title?, callTypeId?, captureMode?, captureApp? })` is the REST helper `start()` uses. `captureMode` defaults to `open_mic`.

`listCallTypes({ apiBase, apiKey })` returns `{ id, name, slug, is_default }[]` so you can pick a type instead of the instance default.

## Create on your server

`POST /api/meetings` from a browser is subject to the instance `CORS_ORIGINS`. The WebSocket is not. If create fails with a network/CORS error, add your origin in Corella or create the meeting from your backend and only open the socket in the page:

```ts
const session = await CorellaLive.connect({
  apiBase: "https://corella.example.com",
  apiKey: "sk_live_…",
  meetingId,
});
```

## Live copilot

Each `onCopilot` event is one coaching cycle (not every transcript line):

| Field | Meaning |
| --- | --- |
| `coach_score` | 0–100, or `null` if the model omitted it |
| `sentiment` | Warmth of the room, not deal outcome. One of `Hostile`, `Tense`, `Frustrated`, `Skeptical`, `Neutral`, `Engaged`, `Positive`, `Enthusiastic`, or `null` if unknown / junk. Never coerced to `Neutral`. |
| `speaker_share` | Timed talk split `[{ label, pct }, …]`, or `null` until there is timed speech. Labels match the transcript (`Me`, a name, `Speaker N`). |
| `suggestion` | Current coaching line, or `null` |
| `blockers` | Open obstacles |
| `action_items` | Next steps spotted so far |

Helpers for a Corella-shaped gauge:

```ts
import { parseSentiment, sentimentFill, SENTIMENTS } from "corella-live";

const sentiment = parseSentiment(c.sentiment); // closed set or null
const fill = sentiment ? sentimentFill(sentiment) : 0; // 0 = Hostile, 1 = Enthusiastic
```

Talk share also updates from transcript durations if you want the bar to move on every utterance instead of waiting for the next copilot frame: sum `end_ms - start_ms` per `speakerLabel` (`me` / `Me` → `Me`).

## Audio

`startMic()` resamples to **PCM16LE mono 16 kHz** in a worklet. To push your own audio (phone bridge, Node, another tab):

```ts
session.sendPcm("me", pcmInt16); // or "them" (channel byte 1)
```

Chunks should already be 16 kHz mono PCM16. No codec negotiation.

## Close codes

| Code | Constant | Meaning |
| --- | --- | --- |
| 4401 | `CLOSE.AUTH` | Bad API key |
| 4404 | `CLOSE.NOT_FOUND` | Meeting missing |
| 4409 | `CLOSE.BUSY_OR_ENDED` | Already live elsewhere, or no longer `recording` |
| 4410 | `CLOSE.DURATION_LIMIT` | This key’s max duration; meeting still finalizes |

## What this package does not do

JWT login, Settings, organization/super-admin, knowledge base, call-type hooks, or Corella’s React UI. API keys are bound to the organization they were created in — they do not follow the owner’s org switcher and still do not reach org/super-admin surfaces.

Wire protocol: [API.md](https://github.com/lucas-oma/corella/blob/main/API.md). In this repo, `server/scripts/api_test_server.py` (`http://localhost:9199/` after `npm run build` here) is a sample site that uses this package the same way an integrator would.
