from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application configuration.

    All values may be overridden via environment variables (see .env.example
    at the repo root). Nothing here should be hardcoded per-deployment.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Corella"
    environment: str = "development"
    log_level: str = "INFO"

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24h

    # When False, self-serve registration (POST /api/auth/register) is
    # disabled and new accounts can only be created by an admin (via
    # POST /api/admin/users). Defaults to open so a fresh single-user
    # instance works out of the box; flip off for admin-managed teams.
    allow_public_registration: bool = True

    # Bootstrap admin account, created on startup if it doesn't already
    # exist. Required to have any admin at all once public registration is
    # turned off (accounts are no longer promoted to admin automatically).
    admin_email: str | None = None
    admin_password: str | None = None
    admin_full_name: str = "Admin"

    # Data stores
    database_url: str = "postgresql+psycopg://corella:corella@localhost:5432/corella"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"

    # Speech models
    hf_token: str | None = None  # required to pull gated pyannote pipelines
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"  # CPU-friendly default

    # Optional cloud STT — instance-wide fallback used when a user hasn't
    # saved their own key via PUT /api/settings/stt. Local faster-whisper
    # above is always the zero-config default either way; this is strictly
    # an opt-in enhancement (see app/services/asr/resolve.py).
    deepgram_api_key: str | None = None
    default_model_deepgram: str = "nova-2"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173"]

    # Audio storage
    audio_storage_path: str = "/data/audio"
    max_audio_upload_mb: int = 500

    # Knowledge base document storage
    kb_storage_path: str = "/data/kb"
    max_kb_upload_mb: int = 50
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Instance-wide LLM provider fallbacks — used when a user hasn't saved
    # their own key via PUT /api/settings/providers/{provider}. Presence of
    # these is what GET /api/settings/providers reports as "connected".
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    ollama_base_url: str | None = None

    # Live recording (app/ws/live_session.py)
    live_vad_aggressiveness: int = 2  # webrtcvad mode 0-3; higher = more aggressive filtering
    live_max_utterance_seconds: int = 12  # force a flush even without a detected pause
    live_min_utterance_ms: int = 300  # ignore speech blips shorter than this

    # Same-room live diarization (app/workers/tasks.py:reconcile_diarization)
    # — periodic full pyannote diarize() passes over a rolling window of
    # already-received per-channel audio, reconciled against a persistent
    # per-channel voice registry (app/services/diarization/cluster.py),
    # rather than clustering one utterance's own embedding in isolation
    # (the old design, removed). Architecture adopted from a reference
    # macOS app's own proven live-diarization design after a real user
    # report of aggressive over-segmentation (up to 8 spurious speakers for
    # a real 2-person call) traced to Phase U's move to Deepgram live
    # streaming: Deepgram's own aggressive endpointing produces many more,
    # much shorter finalized utterances than local VAD ever did, and a
    # short utterance's own embedding is measurably too noisy to safely
    # decide "new speaker" on its own (Phase V found a real same-speaker
    # 0.5s clip scoring 0.53 — under SIMILARITY_THRESHOLD — against its own
    # true speaker). Running the full diarize() pipeline over several
    # seconds of continuous audio at a time, instead of one short/noisy
    # fragment alone, is structurally immune to that failure mode rather
    # than working around it with corroboration/deferral heuristics
    # (Phase V's approach, now removed along with the settings it used).
    #
    # How much already-received per-channel audio one reconciliation pass
    # looks at, trailing backward from "now" — naturally clamped to
    # whatever's actually accumulated so far. Lowered from an initial 45000
    # after a real user report that live labeling felt "not live at all":
    # real production measurements showed a pass over a 45s window taking
    # up to 33s of real worker CPU time, on top of however much of its own
    # interval had already elapsed — the actual reason the *first* label of
    # a call so often arrived only after the call had already ended.
    # Smaller windows measured meaningfully faster in the same production
    # logs (a ~14s window: 6.7-9.2s; a ~40s window: 25.8-33.5s) — 25000 is a
    # deliberate middle point, comfortably above the ~10s reliability floor
    # (diarization_reconcile_min_window_ms) with margin, not a re-measured
    # value of its own yet. See corella.quick_label_hint
    # (app/workers/tasks.py) for the complementary fix that doesn't wait on
    # this pass at all for a voice already confirmed by an earlier one.
    diarization_reconcile_window_ms: int = 25000
    # How often a reconciliation pass runs per active channel. Lowered
    # alongside the window above for the same reason (see that setting's
    # docstring) — a smaller window is cheaper per pass, so a shorter
    # interval no longer means proportionally more worker CPU spent than
    # the original 45s/25s pairing did. Still CPU-bound Python, not the
    # reference app's ANE-accelerated CoreML pipeline (their own interval
    # is 30s, but their pass finishes in low single-digit seconds regardless
    # of interval) — starting point, not tuned against real sustained
    # multi-meeting worker load.
    diarization_reconcile_interval_ms: int = 20000
    # Below this much accumulated per-channel audio, a reconciliation pass
    # doesn't run at all yet — diarize() is unreliable well under ~10s
    # (Phase F-2's own empirical finding, reused here since it's the same
    # pipeline call). Matches the reference app's own measured floor (12s).
    diarization_reconcile_min_window_ms: int = 12000
    # A freshly-registered voice on a channel doesn't get to surface as a
    # real "Speaker N"/"Them N" label purely because it clustered as
    # distinct from anything else seen so far — it needs to hold at least
    # this much real assigned speech first (this app's version of the
    # reference design's "guest folding": a registry entry below this
    # floor stays folded into the channel's plain "Me"/"Them" fallback
    # instead of minting a probably-spurious extra speaker from a
    # short/noisy fragment). The very first voice ever registered on a
    # channel is exempt (nothing to compare it against yet — same
    # precedent the old per-utterance design used for a channel's
    # first-ever cluster), as is any voice recognized against the durable
    # cross-meeting library (Phase O) regardless of how little it's said
    # so far — a resolved identity is real information, not noise.
    #
    # Recalibrated down from the reference app's own 5s/10% (this app's
    # original starting values) after a real, permanently-stuck production
    # case: a genuine second voice on a real ~1-minute call — 3430ms of real
    # assigned speech across 2 real committed segments, not one noisy
    # fragment — cleared neither the old 5000ms absolute floor nor the old
    # 10% share (it was at 9.7%), and since no reconciliation pass ever
    # revisits a meeting once its live session has ended, that's not "still
    # catching up," it's permanent — the segment can never resolve. The
    # reference app's own numbers were tuned for its own (unmeasured here)
    # call-length assumptions, not validated against this app's actual
    # short-test-call usage pattern. Still a single real data point, not
    # exhaustively tuned — but a single stray ~20-25ms fragment (the
    # embedding-crash floor found separately, see live_min_utterance_ms)
    # still can't cross even this lower bar on its own, so the spurious-
    # single-fragment protection this floor exists for is intact.
    diarization_guest_min_ms: int = 2000
    # ...and this much of the channel's total tracked speech so far,
    # whichever is stricter — a voice that's barely spoken at all shouldn't
    # surface even if the call itself is still short. See diarization_
    # guest_min_ms's docstring for the real case this was lowered from 0.10
    # (10%) in response to.
    diarization_guest_min_share: float = 0.08

    # Live copilot (app/services/copilot/live.py, app/ws/live_session.py)
    copilot_trigger_segments: int = 4  # new transcript segments since the last cycle...
    copilot_trigger_seconds: int = 20  # ...or this much elapsed time, whichever first
    copilot_context_window_segments: int = 40  # how much recent transcript feeds each cycle
    copilot_kb_top_k: int = 5

    # Default model per provider, used unless the user picks otherwise (no
    # CallProfile UI yet). The Anthropic default is a verified-current model
    # ID; OpenAI/Gemini/Ollama defaults are my best knowledge without an
    # authoritative source to check against — override these if they're
    # stale by the time you're reading this.
    default_model_anthropic: str = "claude-sonnet-5"
    default_model_openai: str = "gpt-4o-mini"
    default_model_gemini: str = "gemini-3.6-flash"
    default_model_ollama: str = "llama3.2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
