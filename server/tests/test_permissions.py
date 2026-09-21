"""Access-boundary rules: group visibility is report-only inside an org,
org owner/admin get full read of that org, super-admin is the only
cross-org path, writes stay owner-only.
"""

import tempfile

import pytest

from app.models.group import Group, GroupMembership
from app.models.meeting import ActionItem, Channel, Meeting, MeetingStatus, TranscriptSegment
from app.models.organization import Organization, OrgRole


async def _setup(db, make_user):
    """Alice (grouped) owns a ready meeting; Bob shares Alice's group;
    Carol is in the same org but ungrouped; Dana is org admin; a
    super-admin lives in a different org; an outsider is in yet another.
    """
    alice = await make_user(email="alice@example.com")
    organization = await db.get(Organization, alice.active_organization_id)
    assert organization is not None

    group = Group(name="Test Group", organization_id=organization.id)
    db.add(group)
    await db.flush()
    db.add(GroupMembership(user_id=alice.id, group_id=group.id))
    await db.commit()

    bob = await make_user(
        email="bob@example.com", org=organization, org_role=OrgRole.MEMBER, group_ids=[group.id]
    )
    carol = await make_user(email="carol@example.com", org=organization, org_role=OrgRole.MEMBER)
    admin = await make_user(email="admin@example.com", org=organization, org_role=OrgRole.ADMIN)
    super_admin = await make_user(email="super@example.com", is_super_admin=True)
    outsider = await make_user(email="outsider@example.com")

    audio_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    audio_file.write(b"not-real-audio-bytes-but-a-real-file")
    audio_file.close()

    meeting = Meeting(
        owner_id=alice.id,
        organization_id=organization.id,
        title="Alice's meeting",
        status=MeetingStatus.READY,
        audio_path=audio_file.name,
    )
    db.add(meeting)
    await db.commit()
    db.add(
        TranscriptSegment(
            meeting_id=meeting.id, channel=Channel.ME, start_ms=0, end_ms=1000, text="Hello."
        )
    )
    item = ActionItem(meeting_id=meeting.id, text="Follow up")
    db.add(item)
    await db.commit()

    return {
        "alice": alice,
        "bob": bob,
        "carol": carol,
        "admin": admin,
        "super_admin": super_admin,
        "outsider": outsider,
        "meeting_id": str(meeting.id),
        "item_id": str(item.id),
    }


@pytest.mark.asyncio
async def test_owner_can_read_everything(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["alice"])

    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 200
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/transcript", headers=headers)
    ).status_code == 200
    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}/audio", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_group_mate_gets_report_only_not_raw_content(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["bob"])

    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 200
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/action-items", headers=headers)
    ).status_code == 200
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/transcript", headers=headers)
    ).status_code == 404
    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}/audio", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_group_mate_every_write_path_404s(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["bob"])

    assert (await app_client.delete(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 404
    assert (
        await app_client.post(f"/api/meetings/{ctx['meeting_id']}/report", headers=headers)
    ).status_code == 404
    assert (
        await app_client.patch(
            f"/api/meetings/{ctx['meeting_id']}/action-items/{ctx['item_id']}",
            json={"status": "done"},
            headers=headers,
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_ungrouped_org_member_gets_nothing(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["carol"])

    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 404
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/transcript", headers=headers)
    ).status_code == 404
    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}/audio", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_other_org_cannot_see_meeting(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["outsider"])

    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 404
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/transcript", headers=headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_org_admin_gets_full_read_in_this_org(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["admin"])

    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 200
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/transcript", headers=headers)
    ).status_code == 200
    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}/audio", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_org_admin_write_attempts_still_404(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["admin"])

    assert (await app_client.delete(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 404
    assert (
        await app_client.post(f"/api/meetings/{ctx['meeting_id']}/report", headers=headers)
    ).status_code == 404
    assert (
        await app_client.patch(
            f"/api/meetings/{ctx['meeting_id']}/action-items/{ctx['item_id']}",
            json={"status": "done"},
            headers=headers,
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_super_admin_can_read_across_orgs(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["super_admin"])

    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 200
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/transcript", headers=headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_org_all_listing_is_org_admin_only(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)

    admin_response = await app_client.get("/api/meetings/org", headers=auth_headers(ctx["admin"]))
    assert admin_response.status_code == 200
    assert any(m["id"] == ctx["meeting_id"] for m in admin_response.json())

    member_response = await app_client.get("/api/meetings/org", headers=auth_headers(ctx["bob"]))
    assert member_response.status_code == 403


@pytest.mark.asyncio
async def test_instance_all_listing_is_super_admin_only(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)

    super_response = await app_client.get("/api/meetings/all", headers=auth_headers(ctx["super_admin"]))
    assert super_response.status_code == 200
    assert any(m["id"] == ctx["meeting_id"] for m in super_response.json())

    org_admin_response = await app_client.get("/api/meetings/all", headers=auth_headers(ctx["admin"]))
    assert org_admin_response.status_code == 403

    member_response = await app_client.get("/api/meetings/all", headers=auth_headers(ctx["bob"]))
    assert member_response.status_code == 403
