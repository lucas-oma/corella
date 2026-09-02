"""app/services/asr/resolve.py — mirrors test_llm_resolve.py's shape for
the STT side, plus the language-default behavior (the actual Deepgram
"silently assumes English" bug this project found and fixed)."""

import pytest

from app.core.config import get_settings
from app.core.security import encrypt_secret
from app.models.stt_credential import SttCredential
from app.services.asr.resolve import DEFAULT_DEEPGRAM_LANGUAGE, resolve_stt_provider


@pytest.mark.asyncio
async def test_no_deepgram_configured_falls_back_to_local_whisper(db, make_user):
    user = await make_user()
    resolved = await resolve_stt_provider(db, user.id)
    assert resolved.provider == "whisper"
    assert resolved.source == "local"
    assert resolved.api_key is None


@pytest.mark.asyncio
async def test_users_own_credential_resolves_to_deepgram(db, make_user):
    user = await make_user()
    db.add(SttCredential(owner_id=user.id, api_key_encrypted=encrypt_secret("fake-api-key-for-test")))
    await db.commit()

    resolved = await resolve_stt_provider(db, user.id)
    assert resolved.provider == "deepgram"
    assert resolved.source == "user"


@pytest.mark.asyncio
async def test_env_fallback_used_when_no_user_credential(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "deepgram_api_key", "fake-env-key")

    resolved = await resolve_stt_provider(db, user.id)
    assert resolved.provider == "deepgram"
    assert resolved.source == "env"
    assert resolved.api_key == "fake-env-key"


@pytest.mark.asyncio
async def test_default_language_is_multi_not_unset(db, make_user, monkeypatch):
    """The actual regression this test locks in: Deepgram silently assumes
    English and returns an empty transcript if `language` is ever left
    unset — confirmed against a real Deepgram request/response. The
    default here must always be a real, explicit value."""
    user = await make_user()
    monkeypatch.setattr(get_settings(), "deepgram_api_key", "fake-env-key")

    resolved = await resolve_stt_provider(db, user.id)
    assert resolved.language == DEFAULT_DEEPGRAM_LANGUAGE == "multi"


@pytest.mark.asyncio
async def test_explicit_language_preference_applied(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "deepgram_api_key", "fake-env-key")
    user.preferred_stt_language = "es"
    await db.commit()

    resolved = await resolve_stt_provider(db, user.id)
    assert resolved.language == "es"


@pytest.mark.asyncio
async def test_preference_forces_whisper_even_when_deepgram_connected(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "deepgram_api_key", "fake-env-key")
    user.preferred_stt_provider = "whisper"
    await db.commit()

    resolved = await resolve_stt_provider(db, user.id)
    assert resolved.provider == "whisper"


@pytest.mark.asyncio
async def test_deepgram_preference_with_no_connection_falls_back_to_whisper(db, make_user):
    """Preferring Deepgram with no key configured anywhere at all should
    gracefully fall back to local Whisper — Whisper is always available,
    resolve_stt_provider must never fail to return something usable."""
    user = await make_user()
    user.preferred_stt_provider = "deepgram"
    await db.commit()

    resolved = await resolve_stt_provider(db, user.id)
    assert resolved.provider == "whisper"


@pytest.mark.asyncio
async def test_stt_model_preference_override_applied(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "deepgram_api_key", "fake-env-key")
    user.preferred_stt_provider = "deepgram"
    user.preferred_stt_model = "nova-3"
    await db.commit()

    resolved = await resolve_stt_provider(db, user.id)
    assert resolved.model == "nova-3"
