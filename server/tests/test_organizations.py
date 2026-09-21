"""Organization lifecycle: signup, cap, switcher, members, invites, leave/transfer/delete."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_invite_token
from app.models.organization import Organization, OrganizationInvite, OrgRole


@pytest.mark.asyncio
async def test_open_signup_creates_org_and_owner(app_client):
    response = await app_client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "testpass123", "full_name": "Alice"},
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    me = await app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    body = me.json()
    assert body["is_super_admin"] is False
    assert len(body["organizations"]) == 1
    assert body["organizations"][0]["role"] == "owner"
    assert body["organizations"][0]["name"] == "Alice Org"
    assert body["active_organization_id"] == body["organizations"][0]["id"]


@pytest.mark.asyncio
async def test_closed_signup_is_403(app_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_public_registration", False)
    response = await app_client.post(
        "/api/auth/register",
        json={"email": "blocked@example.com", "password": "testpass123", "full_name": "Blocked"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owned_org_cap(app_client, make_user, auth_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_orgs_per_user", 1)
    user = await make_user()
    response = await app_client.post(
        "/api/organizations", json={"name": "Another"}, headers=auth_headers(user)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_org_disabled_when_registration_closed(
    app_client, make_user, auth_headers, monkeypatch
):
    monkeypatch.setattr(get_settings(), "allow_public_registration", False)
    user = await make_user()
    response = await app_client.post(
        "/api/organizations", json={"name": "Nope"}, headers=auth_headers(user)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_switcher_changes_active_org(app_client, make_user, auth_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_orgs_per_user", 2)
    user = await make_user()
    created = await app_client.post(
        "/api/organizations", json={"name": "Second"}, headers=auth_headers(user)
    )
    assert created.status_code == 201
    org_id = created.json()["id"]
    switched = await app_client.put(
        "/api/organizations/current",
        json={"organization_id": org_id},
        headers=auth_headers(user),
    )
    assert switched.status_code == 200
    me = await app_client.get("/api/auth/me", headers=auth_headers(user))
    assert me.json()["active_organization_id"] == org_id


@pytest.mark.asyncio
async def test_org_admin_cannot_demote_owner(app_client, db, make_user, auth_headers):
    owner = await make_user(email="owner@example.com")
    organization = await db.get(Organization, owner.active_organization_id)
    admin = await make_user(
        email="admin@example.com", org=organization, org_role=OrgRole.ADMIN
    )
    response = await app_client.patch(
        f"/api/organizations/{organization.id}/members/{owner.id}",
        json={"role": "member"},
        headers=auth_headers(admin),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_org_admin_cannot_remove_owner(app_client, db, make_user, auth_headers):
    owner = await make_user(email="owner@example.com")
    organization = await db.get(Organization, owner.active_organization_id)
    admin = await make_user(
        email="admin@example.com", org=organization, org_role=OrgRole.ADMIN
    )
    response = await app_client.delete(
        f"/api/organizations/{organization.id}/members/{owner.id}",
        headers=auth_headers(admin),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_last_owner_cannot_leave(app_client, make_user, auth_headers):
    owner = await make_user()
    response = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/leave",
        headers=auth_headers(owner),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_transfer_ownership(app_client, db, make_user, auth_headers):
    owner = await make_user(email="owner@example.com")
    organization = await db.get(Organization, owner.active_organization_id)
    member = await make_user(
        email="member@example.com", org=organization, org_role=OrgRole.MEMBER
    )
    response = await app_client.post(
        f"/api/organizations/{organization.id}/transfer",
        json={"user_id": str(member.id)},
        headers=auth_headers(owner),
    )
    assert response.status_code == 200
    assert response.json()["role"] == "owner"

    # Previous owner is now admin and cannot transfer.
    again = await app_client.post(
        f"/api/organizations/{organization.id}/transfer",
        json={"user_id": str(owner.id)},
        headers=auth_headers(owner),
    )
    assert again.status_code == 403


@pytest.mark.asyncio
async def test_invite_new_user_creates_account_without_personal_org(
    app_client, db, make_user, auth_headers
):
    owner = await make_user(email="owner@example.com")
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "new@example.com", "role": "member"},
        headers=auth_headers(owner),
    )
    assert created.status_code == 201
    token = created.json()["token"]
    assert token

    accept = await app_client.post(
        f"/api/invites/{token}/accept",
        json={"password": "testpass123", "full_name": "New Person"},
    )
    assert accept.status_code == 200
    me = await app_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {accept.json()['access_token']}"}
    )
    body = me.json()
    assert body["email"] == "new@example.com"
    assert len(body["organizations"]) == 1
    assert body["organizations"][0]["id"] == str(owner.active_organization_id)
    assert body["organizations"][0]["role"] == "member"


@pytest.mark.asyncio
async def test_invite_existing_user_requires_login(app_client, db, make_user, auth_headers):
    owner = await make_user(email="owner@example.com")
    existing = await make_user(email="existing@example.com")
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "existing@example.com", "role": "admin"},
        headers=auth_headers(owner),
    )
    token = created.json()["token"]

    logged_out = await app_client.post(f"/api/invites/{token}/accept", json={})
    assert logged_out.status_code == 409

    accepted = await app_client.post(
        f"/api/invites/{token}/accept", json={}, headers=auth_headers(existing)
    )
    assert accepted.status_code == 200
    me = await app_client.get("/api/auth/me", headers=auth_headers(existing))
    orgs = {row["id"]: row["role"] for row in me.json()["organizations"]}
    assert str(owner.active_organization_id) in orgs
    assert orgs[str(owner.active_organization_id)] == "admin"
    assert str(existing.active_organization_id) in orgs or len(orgs) == 2


@pytest.mark.asyncio
async def test_invite_wrong_logged_in_email_is_403(app_client, db, make_user, auth_headers):
    owner = await make_user(email="owner@example.com")
    other = await make_user(email="other@example.com")
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "target@example.com", "role": "member"},
        headers=auth_headers(owner),
    )
    # target doesn't exist yet; accepting while logged in as other is still 403
    # because the invite email doesn't match. Creating the target first:
    target = await make_user(email="target@example.com")
    accepted = await app_client.post(
        f"/api/invites/{created.json()['token']}/accept",
        json={},
        headers=auth_headers(other),
    )
    assert accepted.status_code == 403
    ok = await app_client.post(
        f"/api/invites/{created.json()['token']}/accept",
        json={},
        headers=auth_headers(target),
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_expired_invite_is_gone(app_client, db, make_user, auth_headers):
    owner = await make_user()
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "late@example.com"},
        headers=auth_headers(owner),
    )
    token = created.json()["token"]
    invite = await db.scalar(
        select(OrganizationInvite).where(OrganizationInvite.token_hash == hash_invite_token(token))
    )
    invite.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db.commit()

    preview = await app_client.get(f"/api/invites/{token}")
    assert preview.status_code == 410


@pytest.mark.asyncio
async def test_revoked_invite_is_not_found(app_client, make_user, auth_headers):
    owner = await make_user()
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "gone@example.com"},
        headers=auth_headers(owner),
    )
    invite_id = created.json()["id"]
    token = created.json()["token"]
    revoked = await app_client.delete(
        f"/api/organizations/{owner.active_organization_id}/invites/{invite_id}",
        headers=auth_headers(owner),
    )
    assert revoked.status_code == 204
    preview = await app_client.get(f"/api/invites/{token}")
    assert preview.status_code == 404


@pytest.mark.asyncio
async def test_cannot_invite_as_owner(app_client, make_user, auth_headers):
    owner = await make_user()
    response = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "x@example.com", "role": "owner"},
        headers=auth_headers(owner),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_closed_mode_invite_accept_still_works(
    app_client, make_user, auth_headers, monkeypatch
):
    monkeypatch.setattr(get_settings(), "allow_public_registration", False)
    owner = await make_user()
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "joiner@example.com", "role": "member"},
        headers=auth_headers(owner),
    )
    token = created.json()["token"]
    register = await app_client.post(
        "/api/auth/register",
        json={"email": "joiner@example.com", "password": "testpass123", "full_name": "Joiner"},
    )
    assert register.status_code == 403
    accept = await app_client.post(
        f"/api/invites/{token}/accept",
        json={"password": "testpass123", "full_name": "Joiner"},
    )
    assert accept.status_code == 200


@pytest.mark.asyncio
async def test_delete_org(app_client, make_user, auth_headers):
    await make_user(email="other@example.com", full_name="Other")
    owner = await make_user()
    org_id = owner.active_organization_id
    response = await app_client.delete(
        f"/api/organizations/{org_id}", headers=auth_headers(owner)
    )
    assert response.status_code == 204
    listed = await app_client.get("/api/organizations", headers=auth_headers(owner))
    assert listed.json() == []


@pytest.mark.asyncio
async def test_cannot_delete_instance_org(app_client, make_user, make_org, auth_headers):
    owner = await make_user(create_org=False)
    org = await make_org(owner, name="Default Org", is_instance_org=True)
    await make_user(email="other@example.com", full_name="Other")
    response = await app_client.delete(
        f"/api/organizations/{org.id}", headers=auth_headers(owner)
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Cannot delete the default organization"


@pytest.mark.asyncio
async def test_cannot_delete_last_org(app_client, make_user, auth_headers):
    owner = await make_user()
    response = await app_client.delete(
        f"/api/organizations/{owner.active_organization_id}", headers=auth_headers(owner)
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Cannot delete the last organization"


@pytest.mark.asyncio
async def test_create_invite_emails_when_resend_is_configured(
    app_client, make_user, auth_headers, monkeypatch
):
    sent: list[dict] = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr("app.api.organizations.send_invite_email", fake_send)
    owner = await make_user(email="owner@example.com", full_name="Ada Owner")
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "new@example.com", "role": "member"},
        headers=auth_headers(owner),
    )
    assert created.status_code == 201
    body = created.json()
    assert body["email_sent"] is True
    assert body["token"]
    assert len(sent) == 1
    assert sent[0]["to"] == "new@example.com"
    assert sent[0]["inviter_name"] == "Ada Owner"
    assert sent[0]["role"] == "member"
    assert sent[0]["token"] == body["token"]


@pytest.mark.asyncio
async def test_invite_still_created_when_email_send_fails(
    app_client, make_user, auth_headers, monkeypatch
):
    async def fake_send(**kwargs):
        return False

    monkeypatch.setattr("app.api.organizations.send_invite_email", fake_send)
    owner = await make_user()
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "new@example.com", "role": "admin"},
        headers=auth_headers(owner),
    )
    assert created.status_code == 201
    assert created.json()["email_sent"] is False
    assert created.json()["token"]


@pytest.mark.asyncio
async def test_resend_invite_emails_the_new_token(
    app_client, make_user, auth_headers, monkeypatch
):
    sent: list[str] = []

    async def fake_send(**kwargs):
        sent.append(kwargs["token"])
        return True

    monkeypatch.setattr("app.api.organizations.send_invite_email", fake_send)
    owner = await make_user()
    created = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites",
        json={"email": "new@example.com", "role": "member"},
        headers=auth_headers(owner),
    )
    first = created.json()["token"]
    resent = await app_client.post(
        f"/api/organizations/{owner.active_organization_id}/invites/{created.json()['id']}/resend",
        headers=auth_headers(owner),
    )
    assert resent.status_code == 200
    assert resent.json()["email_sent"] is True
    second = resent.json()["token"]
    assert second != first
    assert sent == [first, second]

