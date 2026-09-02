"""app/services/llm/resolve.py — the priority-order + preference-override
resolution every copilot/report call goes through. Env-fallback cases
monkeypatch attributes directly on the cached Settings instance (it's a
plain mutable pydantic model, not frozen) rather than the environment,
since get_settings() is @lru_cache'd and won't re-read env vars mid-test.
"""

import pytest

from app.core.config import get_settings
from app.core.security import encrypt_secret
from app.models.provider_credential import LLMProvider, ProviderCredential
from app.services.llm.resolve import resolve_provider


@pytest.mark.asyncio
async def test_no_credentials_at_all_resolves_to_none(db, make_user):
    user = await make_user()
    assert await resolve_provider(db, user.id) is None


@pytest.mark.asyncio
async def test_users_own_credential_resolves(db, make_user):
    user = await make_user()
    db.add(ProviderCredential(owner_id=user.id, provider=LLMProvider.OPENAI, api_key_encrypted=encrypt_secret("fake-api-key-for-test")))
    await db.commit()

    resolved = await resolve_provider(db, user.id)
    assert resolved is not None
    assert resolved.provider == LLMProvider.OPENAI


@pytest.mark.asyncio
async def test_priority_order_anthropic_before_openai(db, make_user):
    """Both connected — Anthropic wins, matching _PRIORITY's fixed order."""
    user = await make_user()
    db.add(ProviderCredential(owner_id=user.id, provider=LLMProvider.OPENAI, api_key_encrypted=encrypt_secret("fake-api-key-for-test")))
    db.add(ProviderCredential(owner_id=user.id, provider=LLMProvider.ANTHROPIC, api_key_encrypted=encrypt_secret("fake-api-key-for-test")))
    await db.commit()

    resolved = await resolve_provider(db, user.id)
    assert resolved.provider == LLMProvider.ANTHROPIC


@pytest.mark.asyncio
async def test_env_fallback_used_when_no_user_credential(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake-env-key")

    resolved = await resolve_provider(db, user.id)
    assert resolved is not None
    assert resolved.provider == LLMProvider.ANTHROPIC
    assert resolved.api_key == "fake-env-key"


@pytest.mark.asyncio
async def test_users_own_credential_wins_over_env_fallback(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake-env-key")
    db.add(ProviderCredential(owner_id=user.id, provider=LLMProvider.ANTHROPIC, api_key_encrypted=encrypt_secret("fake-api-key-for-test")))
    await db.commit()

    resolved = await resolve_provider(db, user.id)
    assert resolved.provider == LLMProvider.ANTHROPIC


@pytest.mark.asyncio
async def test_explicit_preference_for_a_connected_provider_wins(db, make_user, monkeypatch):
    """Anthropic is highest-priority and connected, but the user explicitly
    prefers OpenAI (also connected) — the explicit preference should win
    over the fixed priority order."""
    user = await make_user()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake-env-key")
    db.add(ProviderCredential(owner_id=user.id, provider=LLMProvider.OPENAI, api_key_encrypted=encrypt_secret("fake-api-key-for-test")))
    user.preferred_llm_provider = LLMProvider.OPENAI
    await db.commit()

    resolved = await resolve_provider(db, user.id)
    assert resolved.provider == LLMProvider.OPENAI


@pytest.mark.asyncio
async def test_preference_model_override_applied(db, make_user):
    user = await make_user()
    db.add(ProviderCredential(owner_id=user.id, provider=LLMProvider.OPENAI, api_key_encrypted=encrypt_secret("fake-api-key-for-test")))
    user.preferred_llm_provider = LLMProvider.OPENAI
    user.preferred_llm_model = "gpt-5-nano"
    await db.commit()

    resolved = await resolve_provider(db, user.id)
    assert resolved.model == "gpt-5-nano"


@pytest.mark.asyncio
async def test_preference_for_a_disconnected_provider_falls_back_to_auto(db, make_user, monkeypatch):
    """The user prefers Anthropic, but it's not actually connected (no
    credential, no env key) — should gracefully fall back to whatever IS
    connected rather than resolving to nothing / erroring."""
    user = await make_user()
    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://ollama:11434")
    user.preferred_llm_provider = LLMProvider.ANTHROPIC
    await db.commit()

    resolved = await resolve_provider(db, user.id)
    assert resolved is not None
    assert resolved.provider == LLMProvider.OLLAMA
