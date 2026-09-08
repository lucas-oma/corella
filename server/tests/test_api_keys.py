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
