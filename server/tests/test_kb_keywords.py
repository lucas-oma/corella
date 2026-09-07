"""KB-aware transcription accuracy (app/services/embeddings/kb_keywords.py,
app/services/access.py:searchable_kb_keywords) — the LLM extraction call
and the group-scoped Postgres read-side that feeds Deepgram's `keywords`/
faster-whisper's `initial_prompt`. Mirrors test_llm_resolve.py's style for
monkeypatching an env-configured provider, and test_stt_resolve.py's style
for a pure Postgres resolution helper.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.cost import LLMUsageEvent, UsageKind
from app.models.group import Group
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.services.access import searchable_kb_keywords
from app.services.embeddings import kb_keywords as kb_keywords_module
from app.services.embeddings.kb_keywords import extract_keywords_via_llm
from app.services.llm.base import LLMError, LLMResponse
from app.services.llm.resolve import resolve_provider


def _ready_doc(owner_id, keywords=None, status=KBDocumentStatus.READY) -> KBDocument:
    return KBDocument(
        owner_id=owner_id,
        filename="notes.md",
        content_type="text/markdown",
        storage_path="",
        status=status,
        keywords=keywords,
    )


@pytest.mark.asyncio
async def test_searchable_kb_keywords_scoped_to_group(db, make_user):
    group = Group(name="Test Group")
    db.add(group)
    await db.commit()

    alice = await make_user(email="alice@example.com", group_id=group.id)
    bob = await make_user(email="bob@example.com", group_id=group.id)
    carol = await make_user(email="carol@example.com")  # different (no) group

    db.add(_ready_doc(alice.id, keywords=["Corella", "Deepgram"]))
    db.add(_ready_doc(bob.id, keywords=["Nova-3"]))
    db.add(_ready_doc(carol.id, keywords=["ShouldNotLeak"]))
    await db.commit()

    alice_keywords = set(await searchable_kb_keywords(db, alice.id))
    assert alice_keywords == {"Corella", "Deepgram", "Nova-3"}

    carol_keywords = set(await searchable_kb_keywords(db, carol.id))
    assert carol_keywords == {"ShouldNotLeak"}


@pytest.mark.asyncio
async def test_searchable_kb_keywords_dedupes_case_insensitively(db, make_user):
    user = await make_user()
    db.add(_ready_doc(user.id, keywords=["Corella"]))
    db.add(_ready_doc(user.id, keywords=["corella", "Deepgram"]))
    await db.commit()

    keywords = await searchable_kb_keywords(db, user.id)
    assert keywords.count("Corella") + keywords.count("corella") == 1  # first-seen casing kept
    assert "Deepgram" in keywords


@pytest.mark.asyncio
async def test_searchable_kb_keywords_ignores_non_ready_and_null(db, make_user):
    user = await make_user()
    db.add(_ready_doc(user.id, keywords=["PendingTerm"], status=KBDocumentStatus.PENDING))
    db.add(_ready_doc(user.id, keywords=None))
    await db.commit()

    assert await searchable_kb_keywords(db, user.id) == []


@pytest.mark.asyncio
async def test_searchable_kb_keywords_respects_limit(db, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "stt_keyword_limit", 2)
    user = await make_user()
    db.add(_ready_doc(user.id, keywords=["One", "Two", "Three", "Four"]))
    await db.commit()

    assert len(await searchable_kb_keywords(db, user.id)) == 2


@pytest.mark.asyncio
async def test_extract_keywords_via_llm_no_provider_returns_none(db, make_user):
    user = await make_user()
    assert await extract_keywords_via_llm(db, user.id, "some KB document text") is None


@pytest.mark.asyncio
async def test_extract_keywords_via_llm_parses_response_and_logs_cost(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake-env-key")

    async def fake_complete(provider, model, messages, api_key, base_url, max_tokens=1024):
        assert any("mishear" in m.content for m in messages if m.role == "system")
        return LLMResponse(text='{"keywords": ["Corella", "Nova-3"]}', input_tokens=100, output_tokens=20)

    monkeypatch.setattr(kb_keywords_module, "complete", fake_complete)

    keywords = await extract_keywords_via_llm(db, user.id, "Corella integrates with Nova-3.")
    assert keywords == ["Corella", "Nova-3"]

    events = list(
        await db.scalars(select(LLMUsageEvent).where(LLMUsageEvent.owner_id == user.id))
    )
    assert len(events) == 1
    assert events[0].kind == UsageKind.KB_EXTRACTION
    assert events[0].meeting_id is None


@pytest.mark.asyncio
async def test_extract_keywords_via_llm_malformed_json_returns_none(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake-env-key")

    async def fake_complete(provider, model, messages, api_key, base_url, max_tokens=1024):
        return LLMResponse(text="not json at all", input_tokens=10, output_tokens=5)

    monkeypatch.setattr(kb_keywords_module, "complete", fake_complete)

    assert await extract_keywords_via_llm(db, user.id, "text") is None


@pytest.mark.asyncio
async def test_extract_keywords_via_llm_error_returns_none(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake-env-key")

    async def fake_complete(provider, model, messages, api_key, base_url, max_tokens=1024):
        raise LLMError("boom")

    monkeypatch.setattr(kb_keywords_module, "complete", fake_complete)

    assert await extract_keywords_via_llm(db, user.id, "text") is None


# Sanity: resolve_provider itself isn't re-tested here (test_llm_resolve.py
# already owns that), just confirming the env-key monkeypatch actually
# makes a provider resolve, since every test above depends on it.
@pytest.mark.asyncio
async def test_env_key_monkeypatch_actually_resolves(db, make_user, monkeypatch):
    user = await make_user()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake-env-key")
    assert await resolve_provider(db, user.id) is not None
