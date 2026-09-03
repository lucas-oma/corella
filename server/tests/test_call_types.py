"""Admin-managed call types (Phase S): the single-default invariant,
delete blocked on the current default, delete of an in-use non-default
type SET NULLs the referencing meeting without breaking it. The exact
scenarios curl-verified by hand in Phase S, now permanent.
"""

import pytest

from app.models.call_type import CallType
from app.models.meeting import Meeting, MeetingStatus
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_setting_a_new_default_unsets_the_old_one(app_client, db, make_user, auth_headers):
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)
    headers = auth_headers(admin)

    first = await app_client.post(
        "/api/admin/call-types",
        json={"name": "Meeting", "slug": "meeting", "is_default": True},
        headers=headers,
    )
    assert first.status_code == 201
    assert first.json()["is_default"] is True

    second = await app_client.post(
        "/api/admin/call-types",
        json={"name": "Sales call", "slug": "sales", "is_default": True},
        headers=headers,
    )
    assert second.status_code == 201
    assert second.json()["is_default"] is True

    listing = (await app_client.get("/api/admin/call-types", headers=headers)).json()
    defaults = [ct["is_default"] for ct in listing]
    assert defaults.count(True) == 1  # exactly one, ever
    meeting_type = next(ct for ct in listing if ct["slug"] == "meeting")
    assert meeting_type["is_default"] is False


@pytest.mark.asyncio
async def test_deleting_the_current_default_is_blocked(app_client, db, make_user, auth_headers):
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)
    headers = auth_headers(admin)

    created = await app_client.post(
        "/api/admin/call-types",
        json={"name": "Meeting", "slug": "meeting", "is_default": True},
        headers=headers,
    )
    ct_id = created.json()["id"]

    response = await app_client.delete(f"/api/admin/call-types/{ct_id}", headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_deleting_an_in_use_non_default_type_set_nulls_the_meeting(
    app_client, db, make_user, auth_headers
):
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)
    owner = await make_user(email="owner@example.com")

    sales = CallType(name="Sales call", slug="sales", is_default=False)
    db.add(sales)
    await db.commit()

    meeting = Meeting(owner_id=owner.id, title="A sales call", status=MeetingStatus.READY, call_type_id=sales.id)
    db.add(meeting)
    await db.commit()

    response = await app_client.delete(f"/api/admin/call-types/{sales.id}", headers=auth_headers(admin))
    assert response.status_code == 204

    check = await app_client.get(f"/api/meetings/{meeting.id}", headers=auth_headers(owner))
    assert check.status_code == 200  # the meeting itself is untouched
    assert check.json()["call_type"] is None  # just untyped now


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_call_types(app_client, make_user, auth_headers):
    member = await make_user(email="member@example.com")
    response = await app_client.get("/api/admin/call-types", headers=auth_headers(member))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_call_type_listing_omits_admin_only_fields(app_client, db, make_user, auth_headers):
    """GET /api/call-types (no /admin prefix) is for every user — must not
    leak webhook config, just id/name/slug/is_default."""
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)
    member = await make_user(email="member@example.com")

    await app_client.post(
        "/api/admin/call-types",
        json={
            "name": "Sales call",
            "slug": "sales",
            "is_default": True,
            "webhook_enabled": True,
            "webhook_url": "https://example.com/hook",
            "webhook_headers": '{"Authorization": "Bearer super-secret"}',
        },
        headers=auth_headers(admin),
    )

    listing = await app_client.get("/api/call-types", headers=auth_headers(member))
    assert listing.status_code == 200
    row = listing.json()[0]
    assert set(row.keys()) == {"id", "name", "slug", "is_default"}
