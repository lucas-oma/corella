"""Org-scoped call types: the single-default invariant per org, delete
blocked on the current default, delete of an in-use non-default type
SET NULLs the referencing meeting without breaking it.
"""

import pytest

from app.models.call_type import CallType
from app.models.meeting import Meeting, MeetingStatus
from app.models.organization import Organization, OrgRole


@pytest.mark.asyncio
async def test_setting_a_new_default_unsets_the_old_one(app_client, db, make_user, auth_headers):
    admin = await make_user(email="admin@example.com")
    headers = auth_headers(admin)

    first = await app_client.post(
        "/api/admin/call-types",
        json={"name": "Custom A", "slug": "custom-a", "is_default": True},
        headers=headers,
    )
    assert first.status_code == 201
    assert first.json()["is_default"] is True

    second = await app_client.post(
        "/api/admin/call-types",
        json={"name": "Custom B", "slug": "custom-b", "is_default": True},
        headers=headers,
    )
    assert second.status_code == 201
    assert second.json()["is_default"] is True

    listing = (await app_client.get("/api/admin/call-types", headers=headers)).json()
    defaults = [ct["is_default"] for ct in listing]
    assert defaults.count(True) == 1
    first_type = next(ct for ct in listing if ct["slug"] == "custom-a")
    assert first_type["is_default"] is False


@pytest.mark.asyncio
async def test_deleting_the_current_default_is_blocked(app_client, db, make_user, auth_headers):
    admin = await make_user(email="admin@example.com")
    headers = auth_headers(admin)

    created = await app_client.post(
        "/api/admin/call-types",
        json={"name": "Custom default", "slug": "custom-default", "is_default": True},
        headers=headers,
    )
    ct_id = created.json()["id"]

    response = await app_client.delete(f"/api/admin/call-types/{ct_id}", headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_deleting_an_in_use_non_default_type_set_nulls_the_meeting(
    app_client, db, make_user, auth_headers
):
    admin = await make_user(email="admin@example.com")
    organization = await db.get(Organization, admin.active_organization_id)
    owner = await make_user(
        email="owner@example.com", org=organization, org_role=OrgRole.MEMBER
    )

    sales = CallType(
        organization_id=admin.active_organization_id,
        name="Sales call",
        slug="sales-in-use",
        is_default=False,
    )
    db.add(sales)
    await db.commit()

    meeting = Meeting(
        owner_id=owner.id,
        organization_id=owner.active_organization_id,
        title="A sales call",
        status=MeetingStatus.READY,
        call_type_id=sales.id,
    )
    db.add(meeting)
    await db.commit()

    response = await app_client.delete(f"/api/admin/call-types/{sales.id}", headers=auth_headers(admin))
    assert response.status_code == 204

    check = await app_client.get(f"/api/meetings/{meeting.id}", headers=auth_headers(owner))
    assert check.status_code == 200
    assert check.json()["call_type"] is None


@pytest.mark.asyncio
async def test_member_cannot_manage_call_types(app_client, db, make_user, auth_headers):
    owner = await make_user(email="owner@example.com")
    organization = await db.get(Organization, owner.active_organization_id)
    member = await make_user(
        email="member@example.com", org=organization, org_role=OrgRole.MEMBER
    )
    response = await app_client.get("/api/admin/call-types", headers=auth_headers(member))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_call_type_listing_omits_admin_only_fields(app_client, db, make_user, auth_headers):
    admin = await make_user(email="admin@example.com")
    organization = await db.get(Organization, admin.active_organization_id)
    member = await make_user(
        email="member@example.com", org=organization, org_role=OrgRole.MEMBER
    )

    created = await app_client.post(
        "/api/admin/call-types",
        json={
            "name": "Sales special",
            "slug": "sales-special",
            "pre_call_enabled": True,
            "pre_call_url": "https://example.com/hook",
            "pre_call_headers": '{"Authorization": "Bearer super-secret"}',
        },
        headers=auth_headers(admin),
    )
    assert created.status_code == 201

    listing = await app_client.get("/api/call-types", headers=auth_headers(member))
    assert listing.status_code == 200
    row = next(ct for ct in listing.json() if ct["slug"] == "sales-special")
    assert set(row.keys()) == {"id", "name", "slug", "is_default"}


@pytest.mark.asyncio
async def test_admin_call_type_listing_returns_header_templates_not_resolved_values(
    app_client, make_user, auth_headers
):
    admin = await make_user(email="admin@example.com")
    headers = auth_headers(admin)
    template = '{"X-Corella-Webhook-Secret": "{{secret.WEBHOOK_SECRET}}"}'
    created = await app_client.post(
        "/api/admin/call-types",
        json={
            "name": "Sales call",
            "slug": "sales-headers",
            "pre_call_enabled": True,
            "pre_call_url": "https://example.com/lookup",
            "pre_call_headers": template,
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["pre_call_headers"] == template
    assert created.json()["pre_call_async"] is False
    assert created.json()["post_call_async"] is False
    assert "super-secret" not in created.text

    listing = await app_client.get("/api/admin/call-types", headers=headers)
    row = next(ct for ct in listing.json() if ct["slug"] == "sales-headers")
    assert row["pre_call_headers"] == template


@pytest.mark.asyncio
async def test_slug_uniqueness_is_per_org(app_client, db, make_user, auth_headers):
    alice = await make_user(email="alice@example.com")
    bob = await make_user(email="bob@example.com")
    payload = {"name": "Unique-ish", "slug": "shared-slug"}
    assert (
        await app_client.post("/api/admin/call-types", json=payload, headers=auth_headers(alice))
    ).status_code == 201
    clash = await app_client.post("/api/admin/call-types", json=payload, headers=auth_headers(alice))
    assert clash.status_code == 409
    other_org = await app_client.post(
        "/api/admin/call-types", json=payload, headers=auth_headers(bob)
    )
    assert other_org.status_code == 201
