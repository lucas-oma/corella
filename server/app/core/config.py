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
    live_max_utterance_seconds: int = 20  # force a flush even without a detected pause
    live_min_utterance_ms: int = 300  # ignore speech blips shorter than this

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
