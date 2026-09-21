"""Org-admin KB writes, and group-assigned visibility for members."""

import pytest

from app.core.config import get_settings
from app.models.group import Group
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.organization import Organization, OrgRole
from app.services.access import searchable_kb_keywords


def _ready_doc(owner_id, organization_id, *, group_id=None, keywords=None) -> KBDocument:
    return KBDocument(
        owner_id=owner_id,
        organization_id=organization_id,
        group_id=group_id,
        filename="notes.md",
        content_type="text/markdown",
        storage_path="",
        status=KBDocumentStatus.READY,
        keywords=keywords,
    )


@pytest.mark.asyncio
async def test_member_cannot_upload_or_delete(app_client, db, make_user, auth_headers):
    owner = await make_user(email="owner@example.com")
    organization = await db.get(Organization, owner.active_organization_id)
    member = await make_user(
        email="member@example.com", org=organization, org_role=OrgRole.MEMBER
    )
    headers = auth_headers(member)

    upload = await app_client.post(
        "/api/kb/documents",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 403

    doc = _ready_doc(member.id, organization.id)
    db.add(doc)
    await db.commit()

    delete = await app_client.delete(f"/api/kb/documents/{doc.id}", headers=headers)
    assert delete.status_code == 403


@pytest.mark.asyncio
async def test_org_admin_upload_for_group_is_listed_for_members(
    app_client, db, make_user, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "kb_storage_path", str(tmp_path))
    monkeypatch.setattr("app.api.kb.celery_app.send_task", lambda *a, **k: None)

    admin = await make_user(email="admin@example.com")
    organization = await db.get(Organization, admin.active_organization_id)
    group = Group(name="Acme", organization_id=organization.id)
    db.add(group)
    await db.commit()

    member = await make_user(
        email="in-group@example.com",
        org=organization,
        org_role=OrgRole.MEMBER,
        group_ids=[group.id],
    )
    outsider = await make_user(email="out@example.com")

    created = await app_client.post(
        "/api/kb/documents",
        files={"file": ("playbook.md", b"# playbook", "text/markdown")},
        data={"group_id": str(group.id)},
        headers=auth_headers(admin),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["group_id"] == str(group.id)
    assert body["group_name"] == "Acme"

    member_list = await app_client.get("/api/kb/documents", headers=auth_headers(member))
    assert member_list.status_code == 200
    assert [d["id"] for d in member_list.json()] == [body["id"]]

    outsider_list = await app_client.get("/api/kb/documents", headers=auth_headers(outsider))
    assert outsider_list.status_code == 200
    assert outsider_list.json() == []

    admin_list = await app_client.get("/api/kb/documents", headers=auth_headers(admin))
    assert [d["id"] for d in admin_list.json()] == [body["id"]]


@pytest.mark.asyncio
async def test_org_admin_can_delete_any_document_in_org(app_client, db, make_user, auth_headers):
    admin = await make_user(email="admin@example.com")
    organization = await db.get(Organization, admin.active_organization_id)
    owner = await make_user(
        email="owner@example.com", org=organization, org_role=OrgRole.MEMBER
    )
    doc = _ready_doc(owner.id, organization.id)
    db.add(doc)
    await db.commit()

    deleted = await app_client.delete(
        f"/api/kb/documents/{doc.id}", headers=auth_headers(admin)
    )
    assert deleted.status_code == 204

    listed = await app_client.get("/api/kb/documents", headers=auth_headers(admin))
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.asyncio
async def test_group_assigned_keywords_visible_to_members(db, make_user):
    admin = await make_user(email="admin@example.com")
    organization = await db.get(Organization, admin.active_organization_id)
    group = Group(name="Acme", organization_id=organization.id)
    db.add(group)
    await db.commit()

    member = await make_user(
        email="in-group@example.com",
        org=organization,
        org_role=OrgRole.MEMBER,
        group_ids=[group.id],
    )
    db.add(_ready_doc(admin.id, organization.id, group_id=group.id, keywords=["Playbook"]))
    await db.commit()

    assert await searchable_kb_keywords(db, member.id, organization.id) == ["Playbook"]
    # Org owner/admin sees every doc in the org.
    assert await searchable_kb_keywords(db, admin.id, organization.id) == ["Playbook"]
