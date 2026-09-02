from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import decrypt_secret
from app.models.provider_credential import LLMProvider, ProviderCredential
from app.models.user import User

# No CallProfile UI yet (that model stays unused, as it already is today) —
# a fixed priority order stands in for per-user/per-profile provider choice.
_PRIORITY = [LLMProvider.ANTHROPIC, LLMProvider.OPENAI, LLMProvider.GEMINI, LLMProvider.OLLAMA]


@dataclass
class ResolvedProvider:
    provider: LLMProvider
    model: str
    api_key: str | None
    base_url: str | None


def _default_model(provider: LLMProvider, settings: Settings) -> str:
    return {
        LLMProvider.ANTHROPIC: settings.default_model_anthropic,
        LLMProvider.OPENAI: settings.default_model_openai,
        LLMProvider.GEMINI: settings.default_model_gemini,
        LLMProvider.OLLAMA: settings.default_model_ollama,
    }[provider]


def _env_credential(provider: LLMProvider, settings: Settings) -> tuple[str | None, str | None]:
    """(api_key, base_url) from instance-wide .env fallbacks — mirrors the
    logic in app/api/settings.py's _env_configured."""
    if provider == LLMProvider.ANTHROPIC:
        return settings.anthropic_api_key, None
    if provider == LLMProvider.OPENAI:
        return settings.openai_api_key, None
    if provider == LLMProvider.GEMINI:
        return settings.gemini_api_key, None
    if provider == LLMProvider.OLLAMA:
        return None, settings.ollama_base_url
    return None, None


def _resolve_one(
    provider: LLMProvider,
    user_credentials: dict[LLMProvider, ProviderCredential],
    settings: Settings,
    model_override: str | None = None,
) -> ResolvedProvider | None:
    """One provider's connection status, user-credential-first then env
    fallback — None if neither is configured. Shared by the explicit-
    preference check and the priority-order loop below so both apply the
    exact same "is this provider actually usable" logic.
    """
    credential = user_credentials.get(provider)
    if credential is not None:
        api_key = (
            decrypt_secret(credential.api_key_encrypted) if credential.api_key_encrypted else None
        )
        return ResolvedProvider(
            provider=provider,
            model=model_override or _default_model(provider, settings),
            api_key=api_key,
            base_url=credential.base_url,
        )

    env_api_key, env_base_url = _env_credential(provider, settings)
    if env_api_key or env_base_url:
        return ResolvedProvider(
            provider=provider,
            model=model_override or _default_model(provider, settings),
            api_key=env_api_key,
            base_url=env_base_url,
        )

    return None


async def resolve_provider(db: AsyncSession, owner_id: UUID) -> ResolvedProvider | None:
    """Picks the user's explicit preference if they've set one and it's
    actually connected (app/api/settings.py's preferences endpoints),
    otherwise the first connected provider in priority order — the user's
    own saved credential first, falling back to the instance-wide .env
    value. Returns None if nothing is connected at all.
    """
    settings = get_settings()

    user_credentials = {
        c.provider: c
        for c in await db.scalars(
            select(ProviderCredential).where(ProviderCredential.owner_id == owner_id)
        )
    }

    user = await db.get(User, owner_id)
    preferred = user.preferred_llm_provider if user else None
    if preferred is not None:
        resolved = _resolve_one(preferred, user_credentials, settings, user.preferred_llm_model)
        if resolved is not None:
            return resolved
        # Preferred but not actually connected (no saved key, no env
        # fallback) — fall through to auto rather than hard-failing, same
        # graceful-degradation philosophy as every other resolution path in
        # this codebase; a stale preference self-heals instead of blocking
        # the copilot entirely.

    for provider in _PRIORITY:
        resolved = _resolve_one(provider, user_credentials, settings)
        if resolved is not None:
            return resolved

    return None
