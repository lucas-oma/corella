# corella-live

Client for [Corella](https://github.com/lucas-oma/corella)'s live recording API. Install it in your own site, stream a microphone (or raw PCM), and get a speaker-labeled transcript plus live copilot events.

It is the same protocol as the in-repo test page (`server/scripts/api_test_server.py`). Speaker names are kept **off** the transcript object so a later unlabeled `transcript` frame cannot wipe Deepgram splits.

Wire protocol and every quirk: [`API.md`](../../API.md) in this repo. The sample page at `http://localhost:9199/` (see `server/scripts/api_test_server.py`) imports this build — dogfood after `npm run build`.

## Install

Not on the public npm registry yet. From this repo:

```bash
npm install ./packages/corella-live
```

Or after you publish:

```bash
npm install corella-live
```

Requires a Corella instance, an API key (`Settings → API keys`, `sk_live_…`), and a browser (or anything with `fetch` + `WebSocket`).

## Browser example

```ts
import { CorellaLive } from "corella-live";

const session = await CorellaLive.start({
  apiBase: "https://corella.example.com",  // or http://localhost:8090
  apiKey: "sk_live_…",
  title: "Support call",
});

session.onTranscript((lines) => {
  // lines[i].speakerLabel, .text, .start_ms — already merged
  render(lines);
});
session.onPartial((p) => showDraft(p.me));
session.onCopilot((c) => showCoach(c));
session.onClose(({ code, reason }) => {
  // 4410 = this key's max duration; meeting still finalizes
  console.log("closed", code, reason);
});

await session.startMic();   // channel "me"
// later:
session.stop();             // or session.close() — both finalize
```

Create the meeting on your server (avoids CORS) and only open the socket in the page:

```ts
const session = await CorellaLive.connect({
  apiBase: "https://corella.example.com",
  apiKey: "sk_live_…",
  meetingId,
});
```

`createMeeting({ apiBase, apiKey, title?, callTypeId? })` is the REST helper `start()` uses.

`listCallTypes({ apiBase, apiKey })` returns `{ id, name, slug, is_default }[]` so you can pick a type instead of the instance default. Same CORS rule as create.

## Audio

`startMic()` resamples to **PCM16LE mono 16 kHz** in a worklet. To push your own audio (phone bridge, Node, another tab):

```ts
session.sendPcm("me", pcmInt16);   // or "them" (channel byte 1)
```

Chunks should already be 16 kHz mono PCM16. No codec negotiation.

## CORS

`POST /api/meetings` from a browser is subject to the instance `CORS_ORIGINS`. The WebSocket is not. If create fails with a network/CORS error, either add your site origin to Corella or create the meeting from your backend and call `connect`.

## What this package does not do

JWT login, Settings, organization/super-admin, knowledge base, call-type hooks, or Corella's own React UI. API keys are bound to the organization they were created in — they do not follow the owner's org switcher and still do not reach org/super-admin surfaces. See the integration surface in `API.md`.
