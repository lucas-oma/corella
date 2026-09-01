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
