from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models.stt_credential import SttCredential
from app.models.user import User

DEFAULT_DEEPGRAM_LANGUAGE = "multi"


@dataclass
class ResolvedStt:
    provider: str  # "deepgram" | "whisper"
    model: str
    api_key: str | None  # None for whisper — it's local, no key needed
    source: str  # "user" | "env" | "local"
    # Only meaningful when provider == "deepgram" — Deepgram's `language`
    # request param. Defaults to "multi" (automatic multi-language/
    # code-switching detection) rather than leaving it unset: leaving it
    # unset makes Deepgram silently assume English and return an empty
    # transcript on non-English (or just heavily-accented) audio — a real
    # bug found and fixed this round, not a hypothetical.
    language: str = DEFAULT_DEEPGRAM_LANGUAGE


async def resolve_stt_provider(db: AsyncSession, owner_id: UUID) -> ResolvedStt:
    """The user's explicit preference (app/api/settings.py's preferences
    endpoints) if set and actually connected; otherwise Deepgram (the
    user's own saved key, then an instance-wide .env fallback) if
    configured, else always local faster-whisper — mirrors
    app/services/llm/resolve.py's exact two-tier shape, but unlike that
    one this never returns None: Whisper is always available, so
    self-hosted stays the zero-config default and Deepgram is strictly an
    opt-in enhancement, the same positioning Ollama/the LLM providers
    already have relative to the hosted-cloud options.
    """
    settings = get_settings()
    user = await db.get(User, owner_id)

    credential = await db.scalar(select(SttCredential).where(SttCredential.owner_id == owner_id))
    deepgram_key, deepgram_source = (
        (decrypt_secret(credential.api_key_encrypted), "user")
        if credential is not None
        else (settings.deepgram_api_key, "env") if settings.deepgram_api_key else (None, None)
    )

    language = (user.preferred_stt_language if user else None) or DEFAULT_DEEPGRAM_LANGUAGE
    preferred = user.preferred_stt_provider if user else None

    if preferred == "whisper":
        return ResolvedStt(provider="whisper", model=settings.whisper_model, api_key=None, source="local")

    if preferred == "deepgram" and deepgram_key:
        return ResolvedStt(
            provider="deepgram",
            model=(user.preferred_stt_model if user else None) or settings.default_model_deepgram,
            api_key=deepgram_key,
            source=deepgram_source,
            language=language,
        )
    # A "deepgram" preference with no actual connection falls through to
    # the auto behavior below rather than hard-failing — same
    # graceful-degradation philosophy as resolve_provider().

    if deepgram_key:
        return ResolvedStt(
            provider="deepgram",
            model=settings.default_model_deepgram,
            api_key=deepgram_key,
            source=deepgram_source,
            language=language,
        )

    return ResolvedStt(provider="whisper", model=settings.whisper_model, api_key=None, source="local")
