from pydantic import BaseModel

from app.models.provider_credential import LLMProvider


class ProviderStatus(BaseModel):
    provider: LLMProvider
    connected: bool
    # Where the connection comes from — a key/host the user saved themselves
    # (Settings UI, Phase C), an instance-wide fallback from .env, or neither.
    source: str | None  # "user" | "env" | None
