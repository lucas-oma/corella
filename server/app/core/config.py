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
    # A whole-utterance embedding that doesn't confidently match an existing
    # cluster on this channel (or is genuinely clipped, regardless of match)
    # doesn't get to mint a brand-new speaker on the strength of that one
    # verdict alone — a second, wider-window look gets a chance to find a
    # genuinely better embedding first (app/workers/tasks.py:
    # diarize_utterance; see diarization_corroboration_window_ms).
    # Originally gated on content thinness alone (speech_ms below a
    # threshold) rather than any miss — reproduced live, against real
    # Deepgram-chunked ground-truth audio, that this was too narrow: a
    # genuinely NOT-thin utterance (1.5-2s of real speech) can still score
    # just under SIMILARITY_THRESHOLD from ordinary embedding variance
    # (real examples: 0.524, 0.544, both against the correct matching
    # speaker), and a thinness-only gate never gives those a second look at
    # all. Deliberately NOT based on raw similarity score to decide whether
    # a *result* should be trusted, only whether one is even attempted —
    # tried scoring-based triggering for that instead and rejected it after
    # a real counter-example: two different genuinely-short real
    # utterances, one a different speaker and one the same speaker caught
    # by a nearby real gap, scored the identical 0.141 against their
    # closest cluster.
    # How much *already-received* same-channel audio (window_pcm, the same
    # buffer diarization_context_window_ms already sizes) that second look
    # is allowed to use, trailing backward from the miss/clipped
    # utterance's own end — naturally clamped to whatever's actually
    # accumulated so far, same as diarization_context_window_ms. Verified
    # empirically: widening by as little as 800ms-1s of real preceding
    # audio already recovered a confident match (0.72-0.84) on a real short
    # same-speaker clip; a real different-speaker utterance sitting right
    # at the start of its own turn correctly recovered nothing extra (there
    # was nothing earlier belonging to it), and a clean 0.78s clip that
    # scored a deceptively low 0.14 on its own — a false negative caused
    # entirely by brevity, not a real mismatch — correctly recovered 2.4s
    # of real matching context and scored 0.70 once corroboration ran.
    diarization_corroboration_window_ms: int = 3000
    # How much real speech content a corroboration window itself needs
    # before the embedding built from it is trusted over the utterance's
    # own — deliberately lower than what would gate attempting it at all
    # (there's no separate "is this worth trying" content floor anymore,
    # see above; this only gates whether what was actually found is good
    # enough to *trust*). Verified empirically — a real corroboration
    # window with 1500ms of recovered speech content was once rejected by
    # this check before it existed, discarding a sim=0.684 confident,
    # correct match purely because it fell 60ms short of that bar; Phase
    # V's own original calibration already showed 1.0s clips reliably
    # scoring 0.77-0.78, well above SIMILARITY_THRESHOLD, which is the real
    # precedent this floor is set from.
    diarization_corroboration_min_speech_ms: int = 1000
    # A more lenient bar than SIMILARITY_THRESHOLD (0.55), used only to
    # retroactively backfill a segment that was left unlabeled earlier in
    # the same meeting (app/workers/tasks.py:diarize_utterance's backfill
    # pass) once a real cluster now exists to check it against — a lower
    # bar is acceptable here specifically because the risk is different
    # from creating a brand-new speaker: worst case a backfilled label is
    # only roughly right, not confidently wrong, and it only ever runs
    # against a segment that already had no label at all. Starting value
    # only — needs the same real-audio validation the other diarization
    # constants here got before being trusted at scale.
    diarization_backfill_similarity_threshold: float = 0.45

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
