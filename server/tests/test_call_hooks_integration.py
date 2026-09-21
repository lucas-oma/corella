"""Real (non-mocked) integration coverage for the pre/post call-type hooks
— app/scripts/api_test_server.py is started here as a real subprocess and
every request in this file goes over real loopback HTTP to it, unlike
test_call_hooks.py's monkeypatched-httpx unit tests. Covers both hooks in
both their "special" (pre_call_use_as_context / post_call_send_full_payload)
and "regular" (plain fetch / custom template) modes, plus the graceful-
failure and timeout paths.
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import encrypt_secret
from app.models.call_type import CallType
from app.models.hook_log import HookLog
from app.models.meeting import Meeting
from app.services.admin.call_hooks import dispatch_post_call, dispatch_pre_call
from app.services.copilot.report import ReportResult

_PORT = 9198
_SERVER_DIR = Path(__file__).resolve().parent.parent  # server/ — api_test_server.py's cwd


@pytest.fixture(scope="module")
def receiver() -> str:
    """Runs the real api_test_server.py app as a real subprocess on real
    loopback HTTP — not an in-process ASGI transport — so a test here
    exercises the exact same httpx.AsyncClient/network path call_hooks.py
    uses against a real external system, not a mock of it.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "scripts.api_test_server:app", "--port", str(_PORT), "--log-level", "warning"],
        cwd=_SERVER_DIR,
    )
    base_url = f"http://127.0.0.1:{_PORT}"
    try:
        for _ in range(50):
            try:
                httpx.get(f"{base_url}/requests", timeout=0.2)
                break
            except httpx.RequestError:
                time.sleep(0.1)
        else:
            proc.terminate()
            pytest.fail("api_test_server did not start in time")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # /slow sleeps 8s; SIGTERM is graceful so uvicorn will wait it
            # out. Don't let that fail the module after the tests already
            # passed.
            proc.kill()
            proc.wait()


@pytest.fixture(autouse=True)
def _clear_receiver_log(receiver):
    httpx.delete(f"{receiver}/requests")


def _received() -> list[dict]:
    return httpx.get(f"http://127.0.0.1:{_PORT}/requests").json()


def _report(**overrides) -> ReportResult:
    defaults = dict(
        title="Real integration test",
        summary="A plain summary.",
        key_topics=["Topic A"],
        sentiment="Positive",
        notable_quotes=[],
        coach_score=80,
        estimated_cost_usd=0.01,
        action_items=[],
        talk_ratio={"me": 50, "them": 50},
    )
    defaults.update(overrides)
    return ReportResult(**defaults)


async def _make_meeting(db, make_user, **call_type_kwargs) -> Meeting:
    user = await make_user()
    call_type = CallType(
        organization_id=user.active_organization_id,
        name="Integration",
        slug=f"integration-{uuid4()}",
        **call_type_kwargs,
    )
    db.add(call_type)
    await db.commit()
    meeting = Meeting(
        owner_id=user.id,
        organization_id=user.active_organization_id,
        title="Real call",
        call_type_id=call_type.id,
    )
    db.add(meeting)
    await db.commit()
    # populate_existing=True, not a plain get() — the identity map already
    # holds this object from the add()/commit() above, and a plain get()
    # would return that same under-loaded instance rather than actually
    # running the mapper's lazy="joined" owner/call_type relationships.
    # Accessing either of those later as a bare synchronous attribute
    # (as call_hooks.py's dispatch_pre_call/dispatch_post_call do) needs
    # them already populated — a real lazy load at that point has no
    # active greenlet context and raises MissingGreenlet.
    return await db.get(Meeting, meeting.id, populate_existing=True)


# --- Pre-call: special mode (pre_call_use_as_context) -----------------


@pytest.mark.asyncio
async def test_pre_call_special_mode_fetches_real_context(db, make_user, receiver):
    meeting = await _make_meeting(
        db, make_user, pre_call_enabled=True, pre_call_url=f"{receiver}/pre", pre_call_use_as_context=True
    )

    context = await dispatch_pre_call(db, meeting)

    assert context is not None and "CRM lookup" in context
    received = _received()
    assert len(received) == 1
    assert received[0]["method"] == "GET"
    assert received[0]["mandatory_headers_present"] == {
        "x-corella-app-url": True,
        "x-corella-meeting-id": True,
        "x-corella-user-id": True,
        "x-corella-org-id": True,
    }
    assert received[0]["headers"]["x-corella-meeting-id"] == str(meeting.id)
    assert received[0]["headers"]["x-corella-user-id"] == str(meeting.owner_id)


# --- Pre-call: regular mode (fires, but the response isn't used as context) --


@pytest.mark.asyncio
async def test_pre_call_regular_mode_still_fires(db, make_user, receiver):
    """pre_call_use_as_context=False doesn't stop the call from firing —
    it only controls whether create_meeting later stores the response on
    Meeting.pre_call_context. dispatch_pre_call itself has no opinion on
    that flag; the caller does (see app/api/meetings.py:create_meeting)."""
    meeting = await _make_meeting(
        db, make_user, pre_call_enabled=True, pre_call_url=f"{receiver}/pre", pre_call_use_as_context=False
    )

    context = await dispatch_pre_call(db, meeting)

    assert context is not None  # the fetch itself still succeeds
    assert len(_received()) == 1  # the request really happened
    # The endpoint-level decision this flag actually controls (see
    # test_create_meeting_respects_use_as_context_flag below) — dispatch_
    # pre_call never touches this column itself either way.
    assert meeting.pre_call_context is None


@pytest.mark.asyncio
async def test_create_meeting_respects_use_as_context_flag(db, make_user, auth_headers, app_client, receiver):
    """The real endpoint-level behavior: POST /api/meetings stores
    pre_call_context only when the flag is on, even though the pre-call
    fires in both cases."""
    user = await make_user()

    on = CallType(
        organization_id=user.active_organization_id,
        name="On",
        slug="pre-context-on",
        pre_call_enabled=True,
        pre_call_url=f"{receiver}/pre",
        pre_call_use_as_context=True,
    )
    off = CallType(
        organization_id=user.active_organization_id,
        name="Off",
        slug="pre-context-off",
        pre_call_enabled=True,
        pre_call_url=f"{receiver}/pre",
        pre_call_use_as_context=False,
    )
    db.add_all([on, off])
    await db.commit()

    r_on = await app_client.post("/api/meetings", json={"title": "On", "call_type_id": str(on.id)}, headers=auth_headers(user))
    r_off = await app_client.post("/api/meetings", json={"title": "Off", "call_type_id": str(off.id)}, headers=auth_headers(user))
    assert r_on.status_code == 201 and r_off.status_code == 201

    meeting_on = await db.get(Meeting, r_on.json()["id"])
    meeting_off = await db.get(Meeting, r_off.json()["id"])
    assert meeting_on.pre_call_context is not None and "CRM lookup" in meeting_on.pre_call_context
    assert meeting_off.pre_call_context is None
    assert len(_received()) == 2  # both fired
    logs = list(await db.scalars(select(HookLog).where(HookLog.meeting_id == meeting_on.id)))
    assert len(logs) == 1 and logs[0].phase == "pre" and logs[0].outcome == "success"


# --- Pre-call: POST with a rendered body template (a real "regular" shape) --


@pytest.mark.asyncio
async def test_pre_call_post_with_body_template(db, make_user, receiver):
    meeting = await _make_meeting(
        db,
        make_user,
        pre_call_enabled=True,
        pre_call_url=f"{receiver}/pre",
        pre_call_method="POST",
        pre_call_body_template='{"lookup_name": "{{owner_name}}", "meeting": "{{meeting_id}}"}',
    )

    await dispatch_pre_call(db, meeting)

    received = _received()
    assert received[0]["method"] == "POST"
    body = json.loads(received[0]["body"])
    assert body["lookup_name"] == meeting.owner.full_name
    assert body["meeting"] == str(meeting.id)


# --- Pre-call: graceful failure and timeout ----------------------------


@pytest.mark.asyncio
async def test_pre_call_failure_is_swallowed(db, make_user, receiver):
    meeting = await _make_meeting(db, make_user, pre_call_enabled=True, pre_call_url=f"{receiver}/fail")
    assert await dispatch_pre_call(db, meeting) is None
    assert len(_received()) == 1  # it did fire — just returned a 500


@pytest.mark.asyncio
async def test_pre_call_timeout_is_swallowed(db, make_user, receiver, monkeypatch):
    monkeypatch.setattr(get_settings(), "pre_call_timeout_seconds", 1.0)
    meeting = await _make_meeting(db, make_user, pre_call_enabled=True, pre_call_url=f"{receiver}/slow")

    started = time.monotonic()
    result = await dispatch_pre_call(db, meeting)
    elapsed = time.monotonic() - started

    assert result is None
    assert elapsed < 4.0  # bounded by the 1s timeout, not the server's 8s sleep


# --- Post-call: special mode (post_call_send_full_payload) -------------


@pytest.mark.asyncio
async def test_post_call_special_mode_sends_full_payload(db, make_user, receiver):
    meeting = await _make_meeting(
        db, make_user, post_call_enabled=True, post_call_url=f"{receiver}/post", post_call_send_full_payload=True
    )

    await dispatch_post_call(db, meeting, _report(summary="Full payload real test."))

    received = _received()
    assert received[0]["method"] == "POST"
    body = json.loads(received[0]["body"])
    assert body["summary"] == "Full payload real test."
    assert set(body.keys()) >= {
        "meeting_id", "transcript", "copilot_insights", "talk_ratio", "estimated_cost_usd", "action_items",
    }


# --- Post-call: regular mode (custom body template, not the full payload) --


@pytest.mark.asyncio
async def test_post_call_regular_mode_sends_only_templated_fields(db, make_user, receiver):
    meeting = await _make_meeting(
        db,
        make_user,
        post_call_enabled=True,
        post_call_url=f"{receiver}/post",
        post_call_send_full_payload=False,
        post_call_body_template='{"id": "{{meeting_id}}", "summary": "{{summary}}"}',
    )

    await dispatch_post_call(db, meeting, _report(summary="Custom template real test."))
    await db.commit()

    received = _received()
    body = json.loads(received[0]["body"])
    assert body == {"id": str(meeting.id), "summary": "Custom template real test."}  # nothing else leaked in
    logs = list(await db.scalars(select(HookLog).where(HookLog.meeting_id == meeting.id)))
    assert len(logs) == 1 and logs[0].phase == "post" and logs[0].outcome == "success"


# --- Mandatory headers, over real HTTP ----------------------------------


@pytest.mark.asyncio
async def test_mandatory_headers_survive_real_dispatch_even_with_spoofing_attempt(db, make_user, receiver):
    meeting = await _make_meeting(
        db,
        make_user,
        post_call_enabled=True,
        post_call_url=f"{receiver}/post",
        post_call_send_full_payload=True,
        post_call_headers_encrypted=encrypt_secret(json.dumps({"X-Corella-Meeting-Id": "spoofed", "X-Team": "growth"})),
    )

    await dispatch_post_call(db, meeting, _report())

    headers = _received()[0]["headers"]
    assert headers["x-corella-meeting-id"] == str(meeting.id)  # never "spoofed"
    assert headers["x-team"] == "growth"  # the admin's own header still gets through
