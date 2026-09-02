"""app/services/admin/webhooks.py::render_template — the JSON-escaping
logic verified by hand during Phase S, now locked in as a real test. A
webhook body template is JSON the admin wrote themselves; a summary
containing a quote or newline must not be able to break it.
"""

import json

import pytest

from app.models.meeting import Meeting, MeetingStatus
from app.services.admin.webhooks import render_template
from app.services.copilot.report import ReportResult


def _report(**overrides) -> ReportResult:
    defaults = dict(
        title="Test Meeting",
        summary="A plain summary.",
        key_topics=["Topic A", "Topic B"],
        sentiment="Positive",
        notable_quotes=[],
        coach_score=88,
        estimated_cost_usd=0.01,
        action_items=[],
        talk_ratio=None,
    )
    defaults.update(overrides)
    return ReportResult(**defaults)


@pytest.mark.asyncio
async def test_basic_placeholders_substitute(db, make_user):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, title="Discovery call", status=MeetingStatus.READY)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    rendered = await render_template(
        db, '{"id": "{{meeting_id}}", "owner": "{{owner_name}}"}', meeting, _report()
    )
    parsed = json.loads(rendered)
    assert parsed["id"] == str(meeting.id)
    assert parsed["owner"] == user.full_name


@pytest.mark.asyncio
async def test_quotes_apostrophes_and_newlines_stay_valid_json(db, make_user):
    """The actual case verified by hand in Phase S: a summary containing a
    double quote, an apostrophe, and a newline must round-trip through
    json.loads back to the exact original string."""
    user = await make_user()
    meeting = Meeting(owner_id=user.id, title="Discovery call", status=MeetingStatus.READY)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    tricky_summary = 'She said "we\'d need it under $10k," then paused.\nA new line too.'
    rendered = await render_template(db, '{"summary": "{{summary}}"}', meeting, _report(summary=tricky_summary))

    parsed = json.loads(rendered)  # must not raise
    assert parsed["summary"] == tricky_summary


@pytest.mark.asyncio
async def test_array_and_number_placeholders_render_as_real_json_types(db, make_user):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, title="Discovery call", status=MeetingStatus.READY)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    rendered = await render_template(
        db,
        '{"key_topics": {{key_topics}}, "coach_score": {{coach_score}}}',
        meeting,
        _report(key_topics=["Budget", "Timeline"], coach_score=73),
    )
    parsed = json.loads(rendered)
    assert parsed["key_topics"] == ["Budget", "Timeline"]
    assert parsed["coach_score"] == 73


@pytest.mark.asyncio
async def test_transcript_placeholder_includes_real_segments(db, make_user):
    from app.models.meeting import Channel, TranscriptSegment

    user = await make_user()
    meeting = Meeting(owner_id=user.id, title="Discovery call", status=MeetingStatus.READY)
    db.add(meeting)
    await db.commit()
    db.add(
        TranscriptSegment(
            meeting_id=meeting.id, channel=Channel.ME, start_ms=0, end_ms=1000, text="Hello there."
        )
    )
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    rendered = await render_template(db, '{"transcript": "{{transcript}}"}', meeting, _report())
    parsed = json.loads(rendered)
    assert "Hello there." in parsed["transcript"]
    assert "Me:" in parsed["transcript"]


@pytest.mark.asyncio
async def test_call_type_placeholder_resolves_to_the_real_name(db, make_user):
    from app.models.call_type import CallType

    user = await make_user()
    call_type = CallType(name="Sales call", slug="sales")
    db.add(call_type)
    await db.commit()

    meeting = Meeting(owner_id=user.id, title="A call", status=MeetingStatus.READY, call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    rendered = await render_template(db, '{"type": "{{call_type}}"}', meeting, _report())
    assert json.loads(rendered)["type"] == "Sales call"
