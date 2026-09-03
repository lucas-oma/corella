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

    # Same-room live diarization (app/workers/tasks.py:diarize_utterance) — how
    # much *already-received* "Me"-channel audio to feed the pipeline for
    # within-utterance speaker-change detection, ending at the utterance's own
    # end (no forward padding needed/available at dispatch time). Verified
    # empirically: pyannote/speaker-diarization-3.1 is unreliable well under
    # 10s (an isolated ~4s clip missed a real speaker change entirely); 12s
    # gives comfortable margin above that floor.
    diarization_context_window_ms: int = 12000
    # Above this cosine similarity to an existing cluster, an utterance's
    # whole-embedding match is confident enough to skip the expensive full
    # diarize() pass entirely (app/workers/tasks.py:diarize_utterance) —
    # deliberately higher than cluster.SIMILARITY_THRESHOLD (0.55, "is this
    # the same person at all"): this is "confident enough that a within-
    # utterance speaker change is implausible," not just "same speaker
    # overall." Sits inside the real measured same-speaker range (0.67-0.75
    # on real recordings) with margin below it, so a genuinely ambiguous
    # match still falls through to the real pipeline.
    diarization_skip_confidence: float = 0.65
    # An utterance shorter than this is too short to trust a lone "doesn't
    # match any existing speaker" verdict — its own embedding is noisy
    # enough to score below cluster.SIMILARITY_THRESHOLD against the
    # *correct* speaker just from being short, not because a new speaker
    # actually started (app/workers/tasks.py:_cluster_and_assign gets a
    # second, wider-window look before minting a new one; see
    # diarization_corroboration_window_ms). Verified empirically against
    # real conversational audio, not guessed: a real same-speaker 0.5s clip
    # scored 0.53 against its own true speaker (just under the 0.55
    # threshold — a genuine near-miss), a 0.3s clip scored 0.08-0.38; both
    # are well under this floor.
    diarization_short_utterance_ms: int = 1500
    # How much *already-received* same-channel audio (window_pcm, the same
    # buffer diarization_context_window_ms already sizes) the second look
    # above is allowed to use, trailing backward from the short utterance's
    # own end — naturally clamped to whatever's actually accumulated so
    # far, same as diarization_context_window_ms. Verified empirically: on
    # the same real short clips above, widening by as little as 800ms-1s of
    # real preceding audio already recovered a confident match (0.72-0.84);
    # this leaves comfortable margin above that.
    diarization_corroboration_window_ms: int = 3000

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
