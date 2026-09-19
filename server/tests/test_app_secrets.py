"""Admin-managed named secrets (Settings → Secrets): values never leave
the API, names are unique, members cannot touch the vault.
"""

from uuid import UUID

import pytest

from app.core.security import decrypt_secret
from app.models.app_secret import AppSecret
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_admin_can_create_list_update_and_delete_a_secret(app_client, db, make_user, auth_headers):
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)
    headers = auth_headers(admin)

    created = await app_client.post(
        "/api/admin/secrets",
        json={"name": "WEBHOOK_SECRET", "value": "super-secret-value"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "WEBHOOK_SECRET"
    assert "value" not in body
    assert "value_encrypted" not in body
    secret_id = body["id"]

    stored = await db.get(AppSecret, UUID(secret_id))
    assert stored is not None
    assert decrypt_secret(stored.value_encrypted) == "super-secret-value"

    listing = await app_client.get("/api/admin/secrets", headers=headers)
    assert listing.status_code == 200
    assert [row["name"] for row in listing.json()] == ["WEBHOOK_SECRET"]
    assert "value" not in listing.json()[0]

    renamed = await app_client.patch(
        f"/api/admin/secrets/{secret_id}",
        json={"name": "BASET_WEBHOOK"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "BASET_WEBHOOK"
    assert "value" not in renamed.json()

    await db.refresh(stored)
    assert decrypt_secret(stored.value_encrypted) == "super-secret-value"  # keep on name-only patch

    rotated = await app_client.patch(
        f"/api/admin/secrets/{secret_id}",
        json={"value": "rotated-value"},
        headers=headers,
    )
    assert rotated.status_code == 200
    await db.refresh(stored)
    assert decrypt_secret(stored.value_encrypted) == "rotated-value"

    deleted = await app_client.delete(f"/api/admin/secrets/{secret_id}", headers=headers)
    assert deleted.status_code == 204
    assert (await app_client.get("/api/admin/secrets", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_duplicate_secret_name_is_rejected(app_client, make_user, auth_headers):
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)
    headers = auth_headers(admin)
    first = await app_client.post(
        "/api/admin/secrets",
        json={"name": "WEBHOOK_SECRET", "value": "one"},
        headers=headers,
    )
    assert first.status_code == 201

    clash = await app_client.post(
        "/api/admin/secrets",
        json={"name": "WEBHOOK_SECRET", "value": "two"},
        headers=headers,
    )
    assert clash.status_code == 409


@pytest.mark.asyncio
async def test_invalid_secret_name_is_rejected(app_client, make_user, auth_headers):
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)
    response = await app_client.post(
        "/api/admin/secrets",
        json={"name": "not a name", "value": "x"},
        headers=auth_headers(admin),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_empty_value_on_update_is_rejected(app_client, make_user, auth_headers):
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)
    headers = auth_headers(admin)
    created = await app_client.post(
        "/api/admin/secrets",
        json={"name": "WEBHOOK_SECRET", "value": "keep-me"},
        headers=headers,
    )
    response = await app_client.patch(
        f"/api/admin/secrets/{created.json()['id']}",
        json={"value": ""},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_member_cannot_manage_secrets(app_client, make_user, auth_headers):
    member = await make_user(email="member@example.com")
    response = await app_client.get("/api/admin/secrets", headers=auth_headers(member))
    assert response.status_code == 403
