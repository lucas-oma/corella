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

Speaker diarization uses a gated pyannote.audio pipeline — accept its terms on Hugging Face and set `HF_TOKEN` in `.env` before the worker can download it.

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

This is an early-stage build. See the repo's plan history for the phased roadmap — foundations (auth, data model, UI shell) are in place; the recording/transcription/diarization pipeline, the LLM copilot, and the knowledge base are landing next.

## Environment variables

See `.env.example` for the full list: data store URLs, JWT secret, Hugging Face token (for diarization models), default Whisper model/compute type, and optional instance-wide LLM provider keys.
