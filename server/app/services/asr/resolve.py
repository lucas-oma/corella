from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models.stt_credential import SttCredential


@dataclass
class ResolvedStt:
    provider: str  # "deepgram" | "whisper"
    model: str
    api_key: str | None  # None for whisper — it's local, no key needed
    source: str  # "user" | "env" | "local"


async def resolve_stt_provider(db: AsyncSession, owner_id: UUID) -> ResolvedStt:
    """Deepgram (the user's own saved key, then an instance-wide .env
    fallback) if configured, else always local faster-whisper — mirrors
    app/services/llm/resolve.py's exact two-tier shape, but unlike that
    one this never returns None: Whisper is always available, so
    self-hosted stays the zero-config default and Deepgram is strictly an
    opt-in enhancement, the same positioning Ollama/the LLM providers
    already have relative to the hosted-cloud options.
    """
    settings = get_settings()

    credential = await db.scalar(select(SttCredential).where(SttCredential.owner_id == owner_id))
    if credential is not None:
        return ResolvedStt(
            provider="deepgram",
            model=settings.default_model_deepgram,
            api_key=decrypt_secret(credential.api_key_encrypted),
            source="user",
        )

    if settings.deepgram_api_key:
        return ResolvedStt(
            provider="deepgram",
            model=settings.default_model_deepgram,
            api_key=settings.deepgram_api_key,
            source="env",
        )

    return ResolvedStt(provider="whisper", model=settings.whisper_model, api_key=None, source="local")
