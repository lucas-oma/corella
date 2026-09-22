"""Live capture vs report digest on action_items.source."""

import pytest
from sqlalchemy import select

from app.models.meeting import (
    ActionItem,
    ActionItemSource,
    ActionItemStatus,
    Channel,
    Meeting,
    TranscriptSegment,
)
from app.models.provider_credential import LLMProvider
from app.services.copilot import live as live_module
from app.services.copilot import report as report_module
from app.services.copilot.action_items import persist_new_action_items, replace_report_action_items
from app.services.copilot.live import run_cycle
from app.services.copilot.report import generate_report
from app.services.llm.base import LLMResponse
from app.services.llm.resolve import ResolvedProvider


def _fake_provider() -> ResolvedProvider:
    return ResolvedProvider(provider=LLMProvider.ANTHROPIC, model="claude-test", api_key="fake", base_url=None)


@pytest.mark.asyncio
async def test_persist_new_action_items_writes_live_and_skips_substring_dup(db, make_user):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, organization_id=user.active_organization_id, title="A call")
    db.add(meeting)
    await db.commit()

    await persist_new_action_items(db, meeting.id, ["Hire a surveyor", "Present the plan"])
    await persist_new_action_items(db, meeting.id, ["hire a surveyor for the site visit"])
    await db.commit()

    items = list(await db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id)))
    assert {i.text for i in items} == {"Hire a surveyor", "Present the plan"}
    assert all(i.source == ActionItemSource.LIVE for i in items)


@pytest.mark.asyncio
async def test_persist_new_action_items_ignores_report_rows_for_dedup(db, make_user):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, organization_id=user.active_organization_id, title="A call")
    db.add(meeting)
    await db.flush()
    db.add(
        ActionItem(
            meeting_id=meeting.id,
            text="Hire a surveyor",
            status=ActionItemStatus.OPEN,
            source=ActionItemSource.REPORT,
        )
    )
    await db.commit()

    await persist_new_action_items(db, meeting.id, ["Hire a surveyor"])
    await db.commit()

    items = list(await db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id)))
    assert len(items) == 2
    assert {i.source for i in items} == {ActionItemSource.LIVE, ActionItemSource.REPORT}


@pytest.mark.asyncio
async def test_replace_report_action_items_leaves_live_rows(db, make_user):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, organization_id=user.active_organization_id, title="A call")
    db.add(meeting)
    await db.flush()
    db.add(ActionItem(meeting_id=meeting.id, text="Raw live dump", source=ActionItemSource.LIVE))
    db.add(ActionItem(meeting_id=meeting.id, text="Old digest", source=ActionItemSource.REPORT))
    await db.commit()

    created = await replace_report_action_items(db, meeting.id, ["Hire a surveyor", "Hire a surveyor", ""])
    await db.commit()

    assert [i.text for i in created] == ["Hire a surveyor"]
    rows = list(await db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id)))
    assert {i.text for i in rows} == {"Raw live dump", "Hire a surveyor"}
    digest = [i for i in rows if i.source == ActionItemSource.REPORT]
    assert len(digest) == 1 and digest[0].text == "Hire a surveyor"


@pytest.mark.asyncio
async def test_generate_report_writes_digest_not_live_and_sees_captures(db, make_user, monkeypatch):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, organization_id=user.active_organization_id, title="A call")
    db.add(meeting)
    await db.flush()
    db.add(
        TranscriptSegment(
            meeting_id=meeting.id, channel=Channel.ME, start_ms=0, end_ms=1000, text="We should hire a surveyor."
        )
    )
    db.add(
        ActionItem(
            meeting_id=meeting.id,
            text="Contratar topógrafo para la visita",
            source=ActionItemSource.LIVE,
        )
    )
    db.add(
        ActionItem(
            meeting_id=meeting.id,
            text="Contratar/coordinar un topógrafo para la visita al terreno.",
            source=ActionItemSource.LIVE,
        )
    )
    await db.commit()

    seen_user = {}

    async def fake_complete(provider, model, messages, api_key, base_url, max_tokens=1024):
        seen_user["content"] = messages[-1].content
        return LLMResponse(
            text=(
                '{"title": "Survey plan", "summary": "They need a registered surveyor.", '
                '"key_topics": ["Survey"], "sentiment": "Neutral", "notable_quotes": [], '
                '"coach_score": 70, "action_items": ["Hire a RUNC-registered surveyor"]}'
            ),
            input_tokens=40,
            output_tokens=20,
        )

    monkeypatch.setattr(report_module, "complete", fake_complete)

    result = await generate_report(db, meeting, _fake_provider())
    assert [i.text for i in result.action_items] == ["Hire a RUNC-registered surveyor"]
    assert all(i.source == ActionItemSource.REPORT for i in result.action_items)
    assert "Contratar topógrafo" in seen_user["content"]

    rows = list(await db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id)))
    live = [i for i in rows if i.source == ActionItemSource.LIVE]
    report = [i for i in rows if i.source == ActionItemSource.REPORT]
    assert len(live) == 2
    assert [i.text for i in report] == ["Hire a RUNC-registered surveyor"]


@pytest.mark.asyncio
async def test_run_cycle_returns_live_open_items_only(db, make_user, monkeypatch):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, organization_id=user.active_organization_id, title="A call")
    db.add(meeting)
    await db.flush()
    db.add(
        TranscriptSegment(
            meeting_id=meeting.id, channel=Channel.ME, start_ms=0, end_ms=800, text="I'll send the proposal."
        )
    )
    db.add(ActionItem(meeting_id=meeting.id, text="Digest only", source=ActionItemSource.REPORT))
    db.add(ActionItem(meeting_id=meeting.id, text="Already captured", source=ActionItemSource.LIVE))
    await db.commit()

    async def fake_complete(provider, model, messages, api_key, base_url, max_tokens=1024):
        return LLMResponse(
            text='{"suggestion": null, "blockers": [], "action_items": ["Send the proposal"], "coach_score": 80}',
            input_tokens=20,
            output_tokens=10,
        )

    monkeypatch.setattr(live_module, "complete", fake_complete)

    result = await run_cycle(db, meeting.id, user.id, _fake_provider())
    assert result is not None
    assert set(result.action_items) == {"Already captured", "Send the proposal"}

    rows = list(await db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id)))
    sent = [i for i in rows if i.text == "Send the proposal"]
    assert len(sent) == 1 and sent[0].source == ActionItemSource.LIVE


@pytest.mark.asyncio
async def test_generate_report_drops_unknown_sentiment(db, make_user, monkeypatch):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, organization_id=user.active_organization_id, title="A call")
    db.add(meeting)
    await db.flush()
    db.add(
        TranscriptSegment(
            meeting_id=meeting.id, channel=Channel.ME, start_ms=0, end_ms=800, text="Let's wrap up."
        )
    )
    await db.commit()

    async def fake_complete(provider, model, messages, api_key, base_url, max_tokens=1024):
        return LLMResponse(
            text=(
                '{"title": "Wrap up", "summary": "They closed the meeting.", '
                '"key_topics": ["Close"], "sentiment": "Mixed", "notable_quotes": [], '
                '"coach_score": 70, "action_items": []}'
            ),
            input_tokens=40,
            output_tokens=20,
        )

    monkeypatch.setattr(report_module, "complete", fake_complete)

    result = await generate_report(db, meeting, _fake_provider())
    assert result.sentiment is None
    await db.refresh(meeting)
    assert meeting.sentiment is None
