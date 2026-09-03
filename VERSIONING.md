# Versioning

Corella uses three-digit versioning, `MAJOR.MINOR.PATCH`, currently `0.1.0`.
This doc is the reference for which digit to bump when.

## Where the version lives

Two files, and they must move together — bump both by hand on every
version-worthy change, there's no automated sync yet:

- `server/pyproject.toml` — `version = "..."`
- `web/package.json` — `"version": "..."`

## Pre-1.0: what `0.x.y` actually means

While `MAJOR` is `0`, this project makes **no compatibility promise at all**
— not "we'll try," but the standard, explicit meaning of semver's `0.x.y`
range: anything can change in any release, including a breaking one,
without that requiring a `MAJOR` bump. `1.0.0` is the specific line where
"this is now something to build durable things on top of" gets crossed. So
right now:

- `MINOR` is where every meaningful change lands — new features *and*
  breaking ones alike.
- `PATCH` is reserved narrowly, for changes that genuinely don't need
  anyone to think twice.
- `MAJOR` stays `0` no matter how large a single change is, until the
  project deliberately decides to leave `0.x.y` behind.

## When to bump each digit

### `PATCH` — `0.1.0` → `0.1.1`

A pure fix, with nothing new to know about:

- A bug fix with no new user-facing capability and no behavior change
  beyond "the thing that was wrong is now correct." The
  `reconcile_diarization` embedding-crash fix from this session (skip
  turns pyannote produced too short to embed, instead of the whole
  reconciliation pass dying) is the canonical example.
- No new Alembic migration — or, rarely, a purely additive one that rides
  along with a fix rather than existing to support a new feature.
- No new settings/tunables, no new API endpoint, no new UI surface.
- Doc-only corrections, comment/docstring fixes, dependency bumps that
  don't change behavior.

### `MINOR` — `0.1.0` → `0.2.0`

Almost everything else, at this stage:

- Any new feature or capability — this project's own "Phase" unit of work
  (see the plan file, or `docs/AUDIO_PIPELINE.md`'s debugging history) is
  essentially always a `MINOR` bump right now.
- Any new Alembic migration that adds a table/column, even if purely
  additive.
- Any settings/tunable addition, removal, or rename (e.g. this session's
  `diarization_reconcile_*` settings replacing
  `diarization_skip_confidence`/`diarization_corroboration_*`).
- **A breaking change also only bumps `MINOR` here, not `MAJOR`.** This
  session's full diarization-architecture rewrite — `diarize_utterance`
  deleted outright, mid-utterance segment splitting dropped, live-label
  timing changed from near-instant to periodic — would be `0.1.0` →
  `0.2.0`, not a `MAJOR` bump, because pre-1.0 there's no backward-
  compatibility contract yet to break.

### `MAJOR` — first `0.x.y` → `1.0.0`, and beyond once stable

- **Leaving `0.x.y` for `1.0.0` is a deliberate milestone decision, not a
  size or feature-count threshold.** It declares "stable enough to run and
  depend on," not "enough phases shipped." Bump it when you decide the
  project's ready to stop treating every release as provisional — nothing
  in the codebase forces this; it's a call you make.
- **Once at `1.x.y`**, reserve `MAJOR` for an actual breaking change — one
  that requires a person to do something manual to upgrade cleanly: a
  migration that isn't purely additive (drops/renames a column, needs
  backfill judgment — Phase S's call-types migration, with its documented
  lossy downgrade path for custom types, is the concrete precedent for
  what this looks like), a live WS wire-protocol change a running
  frontend/backend pair can't mix versions of, or dropping support for
  something someone might depend on (a provider, a deploy path).

## Tagging a release

Ties into `CONTRIBUTING.md`'s branching model: `main` is the continuously-
moving integration branch and doesn't carry a version bump on every commit;
a version bump happens at the `main` → `release` cut (the existing "stable
cut points live on `release`" convention) — bump both version files in that
PR, then tag `release` at that commit as `vX.Y.Z` once merged.

## Worked examples from this project's own history

To make the rules above concrete against changes that actually happened:

| Change | Bump | Why |
|---|---|---|
| Phase H — groups data model, migration `0004` | `MINOR` | New feature, new (additive) migration. |
| Phase Q — Deepgram as an optional STT provider | `MINOR` | New feature, new settings, new migration. |
| This session's diarization architecture rewrite | `MINOR` | New feature/behavior change — pre-1.0, so breaking is still `MINOR`. |
| This session's `reconcile_diarization` crash fix | `PATCH` | Pure bug fix, no new capability, no schema change. |
| A hypothetical future breaking change to the live WS auth handshake, once at `1.x` | `MAJOR` | Would require every client to update in lockstep — the kind of break `MAJOR` exists for once compatibility is actually promised. |
