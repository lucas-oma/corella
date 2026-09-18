"""API keys (app/models/api_key.py) — self-service credentials letting an
external system act as their owner without a browser login. Covers both
the plain unit-level generate/hash helpers and the real HTTP endpoints
(app/api/settings.py) + the flexible auth dependency
(app/api/deps.py:get_current_user_flexible) that accepts them.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.security import API_KEY_PREFIX, generate_api_key, hash_api_key
from app.models.api_key import ApiKey
from app.models.call_type import CallType
from app.models.meeting import Meeting


def test_generate_api_key_hash_matches_and_plaintext_is_not_the_hash():
    full_key, display_prefix, key_hash = generate_api_key()
    assert full_key.startswith(API_KEY_PREFIX)
    assert display_prefix != full_key  # never the full key
    assert full_key.startswith(display_prefix.rstrip("…"))
    assert key_hash == hash_api_key(full_key)
    assert key_hash != full_key


def test_generate_api_key_is_random_each_time():
    key_a, _, hash_a = generate_api_key()
    key_b, _, hash_b = generate_api_key()
    assert key_a != key_b
    assert hash_a != hash_b


@pytest.mark.asyncio
async def test_create_api_key_returns_plaintext_once_and_never_stores_it(db, make_user, auth_headers, app_client):
    user = await make_user()
    response = await app_client.post(
        "/api/settings/api-keys", json={"name": "Zapier"}, headers=auth_headers(user)
    )
    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith(API_KEY_PREFIX)
    assert body["name"] == "Zapier"
    assert body["max_duration_minutes"] == 60
    assert "key_hash" not in body

    row = await db.scalar(select(ApiKey).where(ApiKey.owner_id == user.id))
    assert row is not None
    assert row.key_hash == hash_api_key(body["key"])
    assert row.key_hash != body["key"]  # the stored form is never the plaintext itself


@pytest.mark.asyncio
async def test_list_and_get_never_leak_the_key(db, make_user, auth_headers, app_client):
    user = await make_user()
    created = await app_client.post(
        "/api/settings/api-keys", json={"name": "Zapier"}, headers=auth_headers(user)
    )
    real_key = created.json()["key"]

    listing = await app_client.get("/api/settings/api-keys", headers=auth_headers(user))
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert "key" not in rows[0]
    assert "key_hash" not in rows[0]
    assert real_key.startswith(rows[0]["key_prefix"].rstrip("…"))  # a prefix, not the whole thing
    assert rows[0]["key_prefix"] != real_key


@pytest.mark.asyncio
async def test_delete_api_key_removes_it_and_is_scoped_to_owner(db, make_user, auth_headers, app_client):
    owner = await make_user(email="owner@example.com")
    other = await make_user(email="other@example.com")
    created = await app_client.post(
        "/api/settings/api-keys", json={"name": "Zapier"}, headers=auth_headers(owner)
    )
    key_id = created.json()["id"]

    # Another user can't delete someone else's key.
    forbidden = await app_client.delete(f"/api/settings/api-keys/{key_id}", headers=auth_headers(other))
    assert forbidden.status_code == 404

    ok = await app_client.delete(f"/api/settings/api-keys/{key_id}", headers=auth_headers(owner))
    assert ok.status_code == 204
    assert await db.get(ApiKey, key_id) is None


@pytest.mark.asyncio
async def test_api_key_authenticates_rest_requests_as_its_owner(db, make_user, app_client):
    user = await make_user()
    full_key, prefix, key_hash = generate_api_key()
    db.add(ApiKey(owner_id=user.id, name="Integration", key_prefix=prefix, key_hash=key_hash))
    await db.commit()

    response = await app_client.post(
        "/api/meetings", json={"title": "Created via API key"}, headers={"Authorization": f"Bearer {full_key}"}
    )
    assert response.status_code == 201
    assert response.json()["title"] == "Created via API key"


@pytest.mark.asyncio
async def test_api_key_can_list_call_types(db, make_user, app_client):
    user = await make_user()
    full_key, prefix, key_hash = generate_api_key()
    db.add(ApiKey(owner_id=user.id, name="Integration", key_prefix=prefix, key_hash=key_hash))
    db.add(CallType(name="Sales", slug="sales-via-key", is_default=True))
    await db.commit()

    response = await app_client.get("/api/call-types", headers={"Authorization": f"Bearer {full_key}"})
    assert response.status_code == 200
    rows = response.json()
    assert any(row["slug"] == "sales-via-key" for row in rows)
    assert set(rows[0].keys()) == {"id", "name", "slug", "is_default"}


@pytest.mark.asyncio
async def test_unknown_api_key_is_rejected(app_client):
    response = await app_client.post(
        "/api/meetings",
        json={"title": "Nope"},
        headers={"Authorization": f"Bearer {API_KEY_PREFIX}not-a-real-key"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_key_last_used_at_updates_on_use(db, make_user, app_client):
    user = await make_user()
    full_key, prefix, key_hash = generate_api_key()
    api_key = ApiKey(owner_id=user.id, name="Integration", key_prefix=prefix, key_hash=key_hash)
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    key_id = api_key.id
    assert api_key.last_used_at is None

    before = datetime.now(UTC)
    response = await app_client.post(
        "/api/meetings", json={"title": "Ping"}, headers={"Authorization": f"Bearer {full_key}"}
    )
    assert response.status_code == 201

    refreshed = await db.get(ApiKey, key_id)
    await db.refresh(refreshed)
    assert refreshed.last_used_at is not None
    assert refreshed.last_used_at >= before.replace(tzinfo=refreshed.last_used_at.tzinfo)


@pytest.mark.asyncio
async def test_meeting_api_key_name_reflects_the_streaming_integration(db, make_user):
    """Meeting.api_key_name (app/models/meeting.py) is what the "Live via
    API"/"Recorded via API" badge reads — None for a browser-recorded
    meeting, the key's own label once one's attached."""
    user = await make_user()
    api_key = ApiKey(owner_id=user.id, name="Zapier", key_prefix="sk_live_zap…", key_hash="x" * 64)
    db.add(api_key)
    await db.commit()

    browser_meeting = Meeting(owner_id=user.id, title="Browser call")
    api_meeting = Meeting(owner_id=user.id, title="API call", api_key_id=api_key.id)
    db.add_all([browser_meeting, api_meeting])
    await db.commit()

    browser_meeting = await db.get(Meeting, browser_meeting.id, populate_existing=True)
    api_meeting = await db.get(Meeting, api_meeting.id, populate_existing=True)
    assert browser_meeting.api_key_name is None
    assert api_meeting.api_key_name == "Zapier"


@pytest.mark.asyncio
async def test_create_api_key_accepts_a_custom_max_duration(db, make_user, auth_headers, app_client):
    user = await make_user()
    response = await app_client.post(
        "/api/settings/api-keys",
        json={"name": "Phone bridge", "max_duration_minutes": 90},
        headers=auth_headers(user),
    )
    assert response.status_code == 201
    assert response.json()["max_duration_minutes"] == 90
    row = await db.scalar(select(ApiKey).where(ApiKey.owner_id == user.id))
    assert row is not None
    assert row.max_duration_minutes == 90


@pytest.mark.asyncio
async def test_create_api_key_rejects_out_of_range_max_duration(make_user, auth_headers, app_client):
    user = await make_user()
    headers = auth_headers(user)
    too_low = await app_client.post(
        "/api/settings/api-keys", json={"name": "X", "max_duration_minutes": 0}, headers=headers
    )
    too_high = await app_client.post(
        "/api/settings/api-keys", json={"name": "X", "max_duration_minutes": 481}, headers=headers
    )
    assert too_low.status_code == 422
    assert too_high.status_code == 422


@pytest.mark.asyncio
async def test_patch_api_key_duration_is_scoped_to_owner(db, make_user, auth_headers, app_client):
    owner = await make_user(email="owner@example.com")
    other = await make_user(email="other@example.com")
    created = await app_client.post(
        "/api/settings/api-keys", json={"name": "Zapier"}, headers=auth_headers(owner)
    )
    key_id = created.json()["id"]
    assert created.json()["max_duration_minutes"] == 60

    forbidden = await app_client.patch(
        f"/api/settings/api-keys/{key_id}",
        json={"max_duration_minutes": 30},
        headers=auth_headers(other),
    )
    assert forbidden.status_code == 404

    ok = await app_client.patch(
        f"/api/settings/api-keys/{key_id}",
        json={"max_duration_minutes": 30},
        headers=auth_headers(owner),
    )
    assert ok.status_code == 200
    assert ok.json()["max_duration_minutes"] == 30
    assert "key" not in ok.json()
    row = await db.get(ApiKey, key_id)
    assert row is not None
    assert row.max_duration_minutes == 30


@pytest.mark.asyncio
async def test_enforce_max_duration_closes_once_elapsed(monkeypatch):
    """The watchdog sleeps the remaining time then closes with 4410 —
    that's what unblocks the live receive loop and runs the same finalize
    path a client-sent stop would. Don't need a real WebSocket for this:
    remaining-time math + the close call are the contract."""
    from app.ws.live_session import WS_CLOSE_DURATION_LIMIT, _enforce_max_duration_loop

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.ws.live_session.asyncio.sleep", fake_sleep)

    class _FakeWebSocket:
        def __init__(self) -> None:
            self.closed: tuple[int, str] | None = None

        async def close(self, code: int = 1000, reason: str = "") -> None:
            self.closed = (code, reason)

    class _FakeSession:
        def __init__(self, elapsed_ms: int) -> None:
            self._elapsed_ms = elapsed_ms

        def elapsed_ms(self) -> int:
            return self._elapsed_ms

    already_over = _FakeWebSocket()
    await _enforce_max_duration_loop(already_over, _FakeSession(90_000), max_duration_ms=60_000)
    assert slept == [0.0]
    assert already_over.closed == (WS_CLOSE_DURATION_LIMIT, "API key max meeting duration reached")

    slept.clear()
    still_running = _FakeWebSocket()
    await _enforce_max_duration_loop(still_running, _FakeSession(50_000), max_duration_ms=60_000)
    assert slept == [10.0]
    assert still_running.closed == (WS_CLOSE_DURATION_LIMIT, "API key max meeting duration reached")
