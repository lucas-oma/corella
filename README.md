<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/src/assets/logo-dark.svg" />
    <img alt="Corella" src="web/src/assets/logo-light.svg" height="72" />
  </picture>
</p>

# Corella

A self-hosted meeting assistant: it records a call from your browser, transcribes and speaker-labels it, keeps a live AI copilot grounded in your own documents, and turns the call into a searchable transcript plus a post-call summary and coaching report. Runs entirely on your own Linux box via Docker Compose — your own database, your own vector store, and (optionally) your own local LLM, with no required cloud dependency beyond whichever hosted LLM provider you choose to connect.

## Architecture

```
                    ┌─────────────┐
   Browser  ───────▶│   web       │  React SPA (nginx)
  (mic + tab audio)  └──────┬──────┘
                             │ REST + WebSocket
                    ┌────────▼────────┐
                    │      api        │  FastAPI
                    └───┬────────┬────┘
                        │        │
              ┌─────────▼──┐   ┌─▼──────────┐
              │  postgres  │   │   redis     │  queue + pub/sub
              └────────────┘   └──────┬──────┘
                                       │
                              ┌────────▼────────┐
                              │     worker       │  Celery: faster-whisper,
                              └────────┬─────────┘  pyannote.audio, embeddings
                                       │
                                ┌──────▼──────┐
                                │   qdrant     │  vector search (knowledge
                                └─────────────┘   base + transcript search)
```

- **api** — FastAPI. Auth, meeting/notes/action-item CRUD, WebSocket audio ingestion and live event push.
- **worker** — Celery. Runs the CPU/GPU-heavy jobs: transcription (faster-whisper), diarization (pyannote.audio), knowledge-base embedding, and post-call report generation, so they never block the API process.
- **postgres** — structured data: users, meetings, transcript segments, notes, action items, call profiles, provider credentials.
- **qdrant** — vector search: knowledge-base document chunks, transcript search, and speaker voice embeddings.
- **redis** — Celery broker/result backend and live-session pub/sub.
- **web** — React/TypeScript SPA, built static and served by nginx.

The copilot's LLM is pluggable per user/call-profile: Anthropic, OpenAI, Gemini (bring your own API key), or a self-hosted Ollama instance.

## Running it

```bash
cp .env.example .env   # fill in JWT_SECRET at minimum
docker compose up --build
```

- Web UI: http://localhost:8080
- API: http://localhost:8000 (docs at `/docs`)

If you have an NVIDIA GPU + the NVIDIA Container Toolkit installed, layer on the GPU override for faster/larger transcription and diarization models:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Speaker diarization uses a gated pyannote.audio pipeline. A valid `HF_TOKEN` alone isn't enough — the Hugging Face account behind it must also individually accept the terms on **each** gated model page the pipeline depends on internally (a separate, one-time click-through per repo — currently three, verified against a real account rather than assumed from docs):

- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/segmentation-3.0
- https://huggingface.co/pyannote/speaker-diarization-community-1

Skipping this doesn't break anything — transcription still works fine and the meeting still finishes, just without speaker labels for uploaded/offline recordings; the pipeline just fails per-file with a clear error in the worker logs instead of loading. Live same-room diarization during a recording (see below) uses a different, non-gated model and works regardless of `HF_TOKEN`/terms acceptance.

## Recording a meeting

Two ways to get a meeting transcribed:

- **Upload**: on the Dashboard, "Upload recording" picks an audio file, which is transcribed (and, if `HF_TOKEN` is set and its gated-model terms are accepted, speaker-diarized) in the background — the meeting page polls and updates itself as processing finishes.
- **Live recording**: "Record live" captures your mic (and, optionally, a shared browser tab's audio) directly in the browser, transcribing each side of the conversation as it happens, with live AI copilot suggestions (if an LLM provider is connected in Settings) and same-room speaker separation on your own mic channel — if more than one voice is detected talking into it, segments get labeled "Speaker 1"/"Speaker 2" live, mid-call, instead of just "Me".

## Access control

By default anyone can create their own account (`ALLOW_PUBLIC_REGISTRATION=true`). For an admin-managed instance:

1. Set `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env` — that account is created automatically on first startup with the `admin` role.
2. Set `ALLOW_PUBLIC_REGISTRATION=false` to close self-serve sign-up.
3. Sign in as the admin and create accounts for everyone else via `POST /api/admin/users` (or `/docs`).

`ADMIN_EMAIL`/`ADMIN_PASSWORD` only ever *create* the account — changing them later and restarting won't touch an existing admin's password.

## Development

Backend:

```bash
cd server
pip install -e ".[worker]"
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend:

```bash
cd web
npm install
npm run dev
```

## Status

This is an early-stage build. See the repo's plan history for the phased roadmap. Done so far: auth and admin-managed accounts, the full data model and UI shell, upload-based transcription/diarization with a transcript + playback view, pluggable LLM providers (Anthropic/OpenAI/Gemini/Ollama) and knowledge-base ingestion, live in-browser recording with a live copilot (suggestions, blockers, action items, coach score) and post-call report generation, and live same-room speaker separation. Landing next: semantic meeting search, post-call re-transcription, admin/multi-user management, per-call cost tracking.

## Environment variables

See `.env.example` for the full list: data store URLs, JWT secret, access control (`ALLOW_PUBLIC_REGISTRATION`, `ADMIN_EMAIL`/`ADMIN_PASSWORD`), audio storage path and upload size cap, Hugging Face token (for diarization models), default Whisper model/compute type, and optional instance-wide LLM provider keys.
