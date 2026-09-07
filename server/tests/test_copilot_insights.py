"""Persisted live-copilot cycles (app/services/copilot/live.py:run_cycle ->
CopilotInsight) — mirrors test_kb_keywords.py's technique of monkeypatching
`complete` to avoid a real LLM call.
"""

import pytest
from sqlalchemy import select

from app.models.meeting import Channel, CopilotInsight, Meeting, TranscriptSegment
from app.models.provider_credential import LLMProvider
from app.services.copilot import live as live_module
from app.services.copilot.live import run_cycle
from app.services.llm.base import LLMResponse
from app.services.llm.resolve import ResolvedProvider


async def _meeting_with_segments(db, owner_id, segments: list[tuple[Channel, int, int, str]]) -> Meeting:
    meeting = Meeting(owner_id=owner_id, title="Test call")
    db.add(meeting)
    await db.flush()
    for channel, start_ms, end_ms, text in segments:
        db.add(
            TranscriptSegment(
                meeting_id=meeting.id, channel=channel, start_ms=start_ms, end_ms=end_ms, text=text
            )
        )
    await db.commit()
    return meeting


def _fake_provider() -> ResolvedProvider:
    return ResolvedProvider(provider=LLMProvider.ANTHROPIC, model="claude-test", api_key="fake", base_url=None)


@pytest.mark.asyncio
async def test_run_cycle_persists_insight_anchored_to_last_segment(db, make_user, monkeypatch):
    user = await make_user()
    meeting = await _meeting_with_segments(
        db,
        user.id,
        [
            (Channel.THEM, 0, 1000, "What's your pricing?"),
            (Channel.ME, 1000, 2500, "Let me check on that."),
        ],
    )

    async def fake_complete(provider, model, messages, api_key, base_url, max_tokens=1024):
        return LLMResponse(
            text='{"suggestion": "Mention the annual discount", "blockers": ["Pricing unresolved"], '
            '"action_items": [], "coach_score": 72}',
            input_tokens=50,
            output_tokens=15,
        )

    monkeypatch.setattr(live_module, "complete", fake_complete)

    result = await run_cycle(db, meeting.id, user.id, _fake_provider())
    assert result is not None
    assert result.suggestion == "Mention the annual discount"
    assert result.coach_score == 72

    insights = list(
        await db.scalars(select(CopilotInsight).where(CopilotInsight.meeting_id == meeting.id))
    )
    assert len(insights) == 1
    insight = insights[0]
    assert insight.at_ms == 2500  # the last segment's end_ms
    assert insight.suggestion == "Mention the annual discount"
    assert insight.blockers == ["Pricing unresolved"]
    assert insight.coach_score == 72


@pytest.mark.asyncio
async def test_run_cycle_persists_score_only_cycle(db, make_user, monkeypatch):
    """No suggestion, no blockers — still persisted, so the score-over-time
    view isn't sparse just because there was nothing else to flag."""
    user = await make_user()
    meeting = await _meeting_with_segments(db, user.id, [(Channel.ME, 0, 800, "Hey, how's it going?")])

    async def fake_complete(provider, model, messages, api_key, base_url, max_tokens=1024):
        return LLMResponse(
            text='{"suggestion": null, "blockers": [], "action_items": [], "coach_score": 85}',
            input_tokens=30,
            output_tokens=10,
        )

    monkeypatch.setattr(live_module, "complete", fake_complete)

    result = await run_cycle(db, meeting.id, user.id, _fake_provider())
    assert result is not None
    assert result.suggestion is None
    assert result.coach_score == 85

    insights = list(
        await db.scalars(select(CopilotInsight).where(CopilotInsight.meeting_id == meeting.id))
    )
    assert len(insights) == 1
    assert insights[0].suggestion is None
    assert insights[0].blockers == []
    assert insights[0].coach_score == 85
