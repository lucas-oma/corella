# Contributing to Corella

Thanks for considering a contribution. This doc covers how to get set up, the conventions the codebase already follows, and what a good PR looks like here.

Before touching any UI code, read [`BRANDING.md`](./BRANDING.md) — it's the single source of truth for colors, typography, and shared components, and PRs that don't follow it will get sent back for that first.

## Getting set up

Fastest path — the full stack via Docker:

```bash
cp .env.example .env   # fill in JWT_SECRET at minimum
docker compose up --build
```

For tighter backend iteration without rebuilding the whole stack:

```bash
cd server
pip install -e ".[worker]"
alembic upgrade head
uvicorn app.main:app --reload
```

```bash
cd web
npm install
npm run dev
```

You'll want real infrastructure to do anything meaningful — `docker compose up postgres redis qdrant` is enough to run the API/worker against locally without pulling in the full Docker build loop.

## Before opening a PR

```bash
cd web && npx tsc -b && npx vite build
cd server && python -m py_compile $(git diff --name-only main -- '*.py')
```

There's no automated test suite yet (see [README's Status section](./README.md#status) — this is a real, open gap, not an oversight, and a PR that adds real `pytest`/`vitest` coverage is welcome on its own). In its absence, every change in this project's history has been verified against a **real, isolated Docker stack** before merging — not the developer's own running instance, and never mocked away. If you're touching backend behavior, do the same:

```bash
docker compose -p corella-verify --env-file <a throwaway .env> up -d postgres redis qdrant
# build and run api/worker against that isolated project, exercise the
# actual endpoint/task with curl or a small script, inspect the real
# database/Qdrant state — then tear it all down:
docker compose -p corella-verify down -v
```

The point is to catch the kind of bug that only shows up against a real database/queue/vector-store round-trip — a migration that doesn't backfill correctly, a race in a Celery task, a response shape that looks right in code review but isn't. Several real bugs in this project's history were only ever caught this way, not by reading the diff.

## Conventions this codebase already follows

Consistency matters more than any individual preference here — match what's already there, especially in a file you didn't write.

**Graceful degradation, everywhere.** No optional integration is ever allowed to take down a meeting. A Deepgram outage falls back to local Whisper mid-call; a missing `HF_TOKEN` skips diarization instead of failing the upload; a broken admin-configured webhook is logged and swallowed, never re-raised; an unconnected LLM provider just skips the copilot/report. If you're adding something optional, follow this pattern — catch the specific failure mode, log it, and let the rest of the pipeline continue. Don't let a new feature become the first thing in this codebase that can take a meeting down.

**Two-tier credential resolution.** Every pluggable provider (LLM or STT) resolves the same way: the signed-in user's own saved credential first, then an instance-wide `.env` fallback, then — for STT specifically — local Whisper, which needs no key at all. See `app/services/llm/resolve.py` and `app/services/asr/resolve.py`. A new provider should slot into this, not invent its own resolution order.

**Secrets are encrypted at rest, always.** Every credential (API keys, webhook headers) goes through `encrypt_secret`/`decrypt_secret` (`app/core/security.py`, Fernet keyed from `JWT_SECRET`) before hitting the database, and is **never** returned by an API response after saving — write-only, same as a password field. If you're adding anything that stores a secret, use these helpers; don't store it in plaintext, and don't add a `GET` that echoes it back.

**Database migrations** live in `server/alembic/versions/`, numbered sequentially (`0013_whatever.py` after the current highest). Follow the existing files for the shape: explicit `sa.Enum(...).create(op.get_bind(), checkfirst=True)` before adding an enum column, a real `downgrade()` that actually reverses the change (not a stub), and a comment explaining *why* wherever the migration does something non-obvious (a data backfill, a lossy downgrade edge case, etc.). Test the migration against a real isolated Postgres — both directions — before opening the PR; a migration that only runs `upgrade()` once against a fresh database hasn't been tested.

## Adding a new LLM provider

The shape is fixed by `app/services/llm/base.py`'s `complete()` dispatcher — every provider implements the same signature and gets dispatched from the same place:

1. Add the provider to the `LLMProvider` enum (`app/models/provider_credential.py`).
2. Write `app/services/llm/<provider>.py` with an async `complete(model, messages, api_key, max_tokens) -> LLMResponse` (`LLMResponse` is `text` + optional `input_tokens`/`output_tokens` for cost tracking — extract real usage from the provider's response if it reports one, `None` if it doesn't; never fabricate a count). Raise `LLMError` on any failure — auth, rate limit, network, unexpected shape.
3. Dispatch it from `base.py`'s `complete()`.
4. Add a default model + pricing entry (`app/services/llm/pricing.py` — real, dated numbers with a source, not a guess) and an env var for the instance-wide fallback key (`app/core/config.py`, `.env.example`).
5. Verify against a **real** account and a real API key — this project has never shipped an LLM integration verified only against documentation. If you genuinely can't get a key, say so explicitly in the PR rather than presenting untested code as verified.

Adding a new STT provider follows the same shape, just against `app/services/asr/resolve.py` and the `WhisperSegment`/`WhisperWord` dataclasses (`app/services/asr/whisper.py`) as the common output shape every downstream consumer expects — see `app/services/asr/deepgram.py` for a complete real example.

## Frontend

- Read `BRANDING.md` first. Every color comes from a Tailwind token, every one has a `dark:` pairing, headlines are `font-serif` and nothing else is, new containers use `.card` and new buttons use `.btn-primary`/`.btn-secondary` rather than one-off styling.
- Copy is plain and specific, no exclamation points, no marketing language — see `BRANDING.md`'s Voice & copy section.
- `tsc -b` and `vite build` must both be clean before a PR.

## Commit messages and PRs

- Write commit messages that explain *why*, not just *what* — this project's own history is full of examples (`git log`) where the message is the only record of a real bug that was found and fixed, not just a diff summary.
- Keep a PR to one coherent change. A migration, its model change, and the endpoint that uses it belong together; an unrelated refactor doesn't.
- State plainly what you verified and how (real accounts vs. shape-only, which stack you ran it against) — don't imply broader testing than actually happened. This project treats an honest "verified by shape, not a live call — no key available" as normal and expected, not a mark against the PR.
- Never commit `.env`, a real credential, or any file under `audio-samples/` — both are gitignored already; don't work around that.

## Reporting bugs / requesting features

Open an issue with what you expected, what actually happened, and how to reproduce it. For a security issue specifically, please don't open a public issue — see the README (or contact the maintainer directly) instead.
