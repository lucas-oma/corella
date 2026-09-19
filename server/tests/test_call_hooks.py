"""app/services/admin/call_hooks.py — the JSON-escaping logic verified by
hand during Phase S (now render_template's own test coverage), plus the
newer pre-call dispatch and the non-negotiable mandatory headers. A hook
body template is JSON the admin wrote themselves; a summary containing a
quote or newline must not be able to break it.
"""

import json

import httpx
import pytest

from app.core.config import get_settings
from app.core.security import encrypt_secret
from app.models.app_secret import AppSecret
from app.models.meeting import ActionItemStatus, Channel, Meeting, MeetingStatus
from app.services.admin.call_hooks import (
    build_full_payload,
    dispatch_pre_call,
    render_pre_call_template,
    render_template,
    request_error_message,
)
from app.services.copilot.report import ReportResult


def test_request_error_message_explains_dns_failure():
    msg = request_error_message(
        httpx.ConnectError("[Errno -2] Name or service not known"),
        "http://host.docker.internal:54321/functions/v1/corella-pre-call",
    )
    assert "host.docker.internal" in msg
    assert "Cannot resolve hostname" in msg


def _report(**overrides) -> ReportResult:
    defaults = dict(
        title="Test Meeting",
        summary="A plain summary.",
        key_topics=["Topic A", "Topic B"],
        sentiment="Positive",
        notable_quotes=["A quote."],
        coach_score=88,
        estimated_cost_usd=0.01,
        action_items=[],
        talk_ratio={"me": 60, "them": 40},
    )
    defaults.update(overrides)
    return ReportResult(**defaults)


class _FakeActionItem:
    def __init__(self, text: str, status: ActionItemStatus):
        self.text = text
        self.status = status


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok"):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    """Monkeypatched in place of httpx.AsyncClient — records the last
    request made and returns whatever response/error the test configured,
    same technique test_kb_keywords.py already uses for `complete`.
    """

    response: _FakeResponse | Exception = _FakeResponse()
    last_call: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, content=None, headers=None):
        _FakeAsyncClient.last_call = {"method": method, "url": url, "content": content, "headers": headers}
        if isinstance(_FakeAsyncClient.response, Exception):
            raise _FakeAsyncClient.response
        return _FakeAsyncClient.response


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
    from app.models.meeting import TranscriptSegment

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


@pytest.mark.asyncio
async def test_full_payload_includes_every_field(db, make_user):
    """The gap this round closed: notable_quotes/estimated_cost_usd/
    talk_ratio/action-item-status/copilot_insights were all previously
    missing from what a hook could ever see."""
    from app.models.meeting import CopilotInsight, TranscriptSegment

    user = await make_user()
    meeting = Meeting(owner_id=user.id, title="Discovery call", status=MeetingStatus.READY, duration_seconds=120)
    db.add(meeting)
    await db.commit()
    db.add(TranscriptSegment(meeting_id=meeting.id, channel=Channel.ME, start_ms=0, end_ms=500, text="Hi."))
    db.add(
        CopilotInsight(meeting_id=meeting.id, at_ms=500, suggestion="Mention the discount", blockers=["Price"], coach_score=70)
    )
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    report = _report(
        notable_quotes=["A real quote."],
        estimated_cost_usd=0.0123,
        talk_ratio={"me": 55, "them": 45},
        action_items=[_FakeActionItem("Follow up", ActionItemStatus.OPEN)],
    )
    payload = await build_full_payload(db, meeting, report)

    assert payload["notable_quotes"] == ["A real quote."]
    assert payload["estimated_cost_usd"] == 0.0123
    assert payload["talk_ratio"] == {"me": 55, "them": 45}
    assert payload["action_items"] == [{"text": "Follow up", "status": "open"}]
    assert payload["copilot_insights"] == [
        {"at_ms": 500, "suggestion": "Mention the discount", "blockers": ["Price"], "coach_score": 70}
    ]
    assert "Hi." in payload["transcript"]
    assert payload["duration_seconds"] == 120


@pytest.mark.asyncio
async def test_full_payload_placeholder_expands_inline(db, make_user):
    user = await make_user()
    meeting = Meeting(owner_id=user.id, title="Discovery call", status=MeetingStatus.READY)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    rendered = await render_template(db, '{"data": {{full_payload}}}', meeting, _report())
    parsed = json.loads(rendered)
    assert parsed["data"]["meeting_id"] == str(meeting.id)
    assert parsed["data"]["summary"] == "A plain summary."


@pytest.mark.asyncio
async def test_dispatch_pre_call_returns_response_text(db, make_user, monkeypatch):
    from app.models.call_type import CallType

    user = await make_user()
    call_type = CallType(
        name="Sales", slug="sales-pre", pre_call_enabled=True, pre_call_url="https://example.com/lookup"
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    _FakeAsyncClient.response = _FakeResponse(200, "fetched context text")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    result = await dispatch_pre_call(db, meeting)
    assert result == "fetched context text"
    assert _FakeAsyncClient.last_call["method"] == "GET"


def test_render_pre_call_template_substitutes_meeting_level_fields():
    """A real case: a POST pre-call whose body needs the meeting's own
    identity — no transcript/report exists yet at this point, so this is
    a smaller placeholder set than render_template's (post-call) one."""
    from uuid import uuid4

    from app.models.call_type import CallType

    call_type = CallType(name="Sales", slug="sales-pre-body")
    meeting = Meeting(
        owner_id=uuid4(),
        title="Discovery call",
        status=MeetingStatus.RECORDING,
        call_type=call_type,
    )
    meeting.owner = type("Owner", (), {"full_name": "Jane Doe"})()

    rendered = render_pre_call_template(
        '{"lookup_name": "{{owner_name}}", "meeting": "{{meeting_id}}", "type": "{{call_type}}"}', meeting
    )
    parsed = json.loads(rendered)
    assert parsed["lookup_name"] == "Jane Doe"
    assert parsed["meeting"] == str(meeting.id)
    assert parsed["type"] == "Sales"


@pytest.mark.asyncio
async def test_dispatch_pre_call_sends_rendered_body_template(db, make_user, monkeypatch):
    from app.models.call_type import CallType

    user = await make_user()
    call_type = CallType(
        name="Sales",
        slug="sales-pre-body-dispatch",
        pre_call_enabled=True,
        pre_call_url="https://example.com/lookup",
        pre_call_method="POST",
        pre_call_body_template='{"owner": "{{owner_name}}"}',
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    _FakeAsyncClient.response = _FakeResponse(200, "ok")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    await dispatch_pre_call(db, meeting)
    sent_body = json.loads(_FakeAsyncClient.last_call["content"])
    assert sent_body["owner"] == user.full_name


@pytest.mark.asyncio
async def test_dispatch_pre_call_swallows_request_errors(db, make_user, monkeypatch):
    from app.models.call_type import CallType

    user = await make_user()
    call_type = CallType(name="Sales", slug="sales-err", pre_call_enabled=True, pre_call_url="https://example.com/down")
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    _FakeAsyncClient.response = httpx.RequestError("connection refused")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    assert await dispatch_pre_call(db, meeting) is None


@pytest.mark.asyncio
async def test_mandatory_headers_cannot_be_overridden_by_custom_headers(db, make_user, monkeypatch):
    """The non-negotiable guarantee: even a custom header that reuses one
    of the three mandatory names loses — the real value always wins."""
    from app.models.call_type import CallType

    user = await make_user()
    call_type = CallType(
        name="Sales",
        slug="sales-headers",
        pre_call_enabled=True,
        pre_call_url="https://example.com/lookup",
        pre_call_headers_encrypted=encrypt_secret(json.dumps({"X-Corella-Meeting-Id": "spoofed", "X-Custom": "1"})),
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    _FakeAsyncClient.response = _FakeResponse(200, "ok")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    await dispatch_pre_call(db, meeting)
    sent_headers = _FakeAsyncClient.last_call["headers"]
    assert sent_headers["X-Corella-Meeting-Id"] == str(meeting.id)  # not "spoofed"
    assert sent_headers["X-Corella-App-Url"] == get_settings().public_app_url
    assert sent_headers["X-Corella-User-Id"] == str(user.id)
    assert sent_headers["X-Custom"] == "1"  # the admin's own header still gets through


@pytest.mark.asyncio
async def test_secret_placeholders_in_headers_are_interpolated(db, make_user, monkeypatch):
    from app.models.call_type import CallType

    user = await make_user()
    db.add(AppSecret(name="WEBHOOK_SECRET", value_encrypted=encrypt_secret("the-real-token")))
    call_type = CallType(
        name="Sales",
        slug="sales-secret-headers",
        pre_call_enabled=True,
        pre_call_url="https://example.com/lookup",
        pre_call_headers_encrypted=encrypt_secret(
            json.dumps({"X-Corella-Webhook-Secret": "{{secret.WEBHOOK_SECRET}}", "X-Team": "growth"})
        ),
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    _FakeAsyncClient.response = _FakeResponse(200, "ok")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    await dispatch_pre_call(db, meeting)
    sent_headers = _FakeAsyncClient.last_call["headers"]
    assert sent_headers["X-Corella-Webhook-Secret"] == "the-real-token"
    assert sent_headers["X-Team"] == "growth"
    assert "{{secret." not in sent_headers["X-Corella-Webhook-Secret"]


@pytest.mark.asyncio
async def test_missing_secret_aborts_pre_call(db, make_user, monkeypatch):
    from app.models.call_type import CallType

    user = await make_user()
    call_type = CallType(
        name="Sales",
        slug="sales-missing-secret",
        pre_call_enabled=True,
        pre_call_url="https://example.com/lookup",
        pre_call_headers_encrypted=encrypt_secret(
            json.dumps({"X-Corella-Webhook-Secret": "{{secret.MISSING}}"})
        ),
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id)

    _FakeAsyncClient.last_call = None
    _FakeAsyncClient.response = _FakeResponse(200, "ok")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    assert await dispatch_pre_call(db, meeting) is None
    assert _FakeAsyncClient.last_call is None


@pytest.mark.asyncio
async def test_dispatch_pre_call_persists_redacted_success_log(db, make_user, monkeypatch):
    from sqlalchemy import select

    from app.models.call_type import CallType
    from app.models.hook_log import HookLog

    user = await make_user()
    db.add(AppSecret(name="WEBHOOK_SECRET", value_encrypted=encrypt_secret("the-real-token")))
    call_type = CallType(
        name="Sales",
        slug="sales-hook-log",
        pre_call_enabled=True,
        pre_call_url="https://example.com/lookup",
        pre_call_headers_encrypted=encrypt_secret(
            json.dumps(
                {
                    "Authorization": "Bearer the-real-token",
                    "X-Corella-Webhook-Secret": "{{secret.WEBHOOK_SECRET}}",
                    "X-Team": "growth",
                }
            )
        ),
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id, populate_existing=True)

    _FakeAsyncClient.response = _FakeResponse(200, '{"ok": true}')
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    assert await dispatch_pre_call(db, meeting) == '{"ok": true}'
    await db.commit()

    logs = list(await db.scalars(select(HookLog).where(HookLog.meeting_id == meeting.id)))
    assert len(logs) == 1
    log = logs[0]
    assert log.phase == "pre"
    assert log.outcome == "success"
    assert log.response_status == 200
    assert log.ran_async is False
    headers = json.loads(log.request_headers)
    assert headers["Authorization"] == "••••"
    assert headers["X-Corella-Webhook-Secret"] == "••••"
    assert headers["X-Team"] == "growth"
    assert "the-real-token" not in (log.request_headers or "")
    assert "the-real-token" not in (log.response_body or "")


@pytest.mark.asyncio
async def test_dispatch_pre_call_persists_error_log(db, make_user, monkeypatch):
    from sqlalchemy import select

    from app.models.call_type import CallType
    from app.models.hook_log import HookLog

    user = await make_user()
    call_type = CallType(
        name="Sales", slug="sales-hook-log-err", pre_call_enabled=True, pre_call_url="https://example.com/down"
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id, populate_existing=True)

    _FakeAsyncClient.response = httpx.RequestError("connection refused")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    assert await dispatch_pre_call(db, meeting) is None
    await db.commit()

    logs = list(await db.scalars(select(HookLog).where(HookLog.meeting_id == meeting.id)))
    assert len(logs) == 1
    assert logs[0].outcome == "error"
    assert "Connection refused" in (logs[0].error or "")
    assert "example.com/down" in (logs[0].error or "")


@pytest.mark.asyncio
async def test_missing_secret_is_logged_and_does_not_fire(db, make_user, monkeypatch):
    from sqlalchemy import select

    from app.models.call_type import CallType
    from app.models.hook_log import HookLog

    user = await make_user()
    call_type = CallType(
        name="Sales",
        slug="sales-missing-secret-log",
        pre_call_enabled=True,
        pre_call_url="https://example.com/lookup",
        pre_call_headers_encrypted=encrypt_secret(json.dumps({"Authorization": "{{secret.MISSING}}"})),
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(owner_id=user.id, title="A call", call_type_id=call_type.id)
    db.add(meeting)
    await db.commit()
    meeting = await db.get(Meeting, meeting.id, populate_existing=True)

    _FakeAsyncClient.last_call = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    assert await dispatch_pre_call(db, meeting) is None
    await db.commit()
    logs = list(await db.scalars(select(HookLog).where(HookLog.meeting_id == meeting.id)))
    assert len(logs) == 1
    assert logs[0].outcome == "error"
    assert "secret" in (logs[0].error or "").lower()


@pytest.mark.asyncio
async def test_hook_logs_endpoint_is_admin_only(app_client, db, make_user, auth_headers, monkeypatch):
    from app.models.call_type import CallType
    from app.models.user import UserRole

    admin = await make_user(email="admin-hooks@example.com", role=UserRole.ADMIN)
    owner = await make_user(email="owner-hooks@example.com")
    call_type = CallType(
        name="Sales", slug="sales-hook-logs-api", pre_call_enabled=True, pre_call_url="https://example.com/lookup"
    )
    db.add(call_type)
    await db.commit()

    _FakeAsyncClient.response = _FakeResponse(200, "fetched")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    created = await app_client.post(
        "/api/meetings",
        json={"title": "Hooked", "call_type_id": str(call_type.id)},
        headers=auth_headers(owner),
    )
    assert created.status_code == 201
    meeting_id = created.json()["id"]

    member_resp = await app_client.get(f"/api/meetings/{meeting_id}/hook-logs", headers=auth_headers(owner))
    assert member_resp.status_code == 403

    admin_resp = await app_client.get(f"/api/meetings/{meeting_id}/hook-logs", headers=auth_headers(admin))
    assert admin_resp.status_code == 200
    logs = admin_resp.json()
    assert len(logs) == 1
    assert logs[0]["phase"] == "pre"
    assert logs[0]["outcome"] == "success"
    assert logs[0]["response_body"] == "fetched"


@pytest.mark.asyncio
async def test_async_pre_call_is_queued_not_awaited(app_client, db, make_user, auth_headers, monkeypatch):
    from app.models.call_type import CallType
    from app.models.meeting import Meeting

    sent: list[tuple] = []
    monkeypatch.setattr(
        "app.api.meetings.celery_app.send_task",
        lambda name, args=None, **_kwargs: sent.append((name, args)),
    )
    _FakeAsyncClient.last_call = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    user = await make_user()
    call_type = CallType(
        name="Sales",
        slug="sales-async-pre",
        pre_call_enabled=True,
        pre_call_url="https://example.com/lookup",
        pre_call_use_as_context=True,
        pre_call_async=True,
    )
    db.add(call_type)
    await db.commit()

    created = await app_client.post(
        "/api/meetings",
        json={"title": "Async pre", "call_type_id": str(call_type.id)},
        headers=auth_headers(user),
    )
    assert created.status_code == 201
    meeting_id = created.json()["id"]
    assert sent == [("corella.dispatch_pre_call", [meeting_id])]
    assert _FakeAsyncClient.last_call is None

    meeting = await db.get(Meeting, meeting_id)
    assert meeting is not None
    assert meeting.pre_call_context is None
