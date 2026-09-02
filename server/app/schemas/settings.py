from pydantic import BaseModel

from app.models.provider_credential import LLMProvider


class ProviderStatus(BaseModel):
    provider: LLMProvider
    connected: bool
    # Where the connection comes from — a key/host the user saved themselves,
    # an instance-wide fallback from .env, or neither.
    source: str | None  # "user" | "env" | None


class ProviderCredentialUpdate(BaseModel):
    """Body for PUT /api/settings/providers/{provider}. Anthropic/OpenAI/
    Gemini expect `api_key`; Ollama expects `base_url`. Never returned back —
    write-only, standard secret-handling UX.
    """

    api_key: str | None = None
    base_url: str | None = None


class SttStatus(BaseModel):
    connected: bool
    source: str | None  # "user" | "env" | None — same shape as ProviderStatus


class SttCredentialUpdate(BaseModel):
    """Body for PUT /api/settings/stt. Write-only, never returned back —
    same secret-handling convention as ProviderCredentialUpdate."""

    api_key: str


class SttOverview(BaseModel):
    active: str  # "deepgram" | "whisper"
    model: str
    source: str  # "user" | "env" | "local"


class LanguageModelOverview(BaseModel):
    active: LLMProvider | None
    model: str | None
    source: str | None  # "user" | "env" | None


class EmbeddingsOverview(BaseModel):
    model: str


class DiarizationOverview(BaseModel):
    pipeline: str
    speaker_embedding: str
    available: bool


class AiOverview(BaseModel):
    """GET /api/settings/ai-overview — what's actually powering each part
    of the app right now for the current user, computed from the same
    resolution functions the app itself calls at runtime, not re-derived
    guesses."""

    speech_to_text: SttOverview
    language_model: LanguageModelOverview
    embeddings: EmbeddingsOverview
    diarization: DiarizationOverview
