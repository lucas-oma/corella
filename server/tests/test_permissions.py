"""The access-boundary rules this project spent multiple phases getting
right (Phase H group visibility, Phase J admin read access): group
visibility is report-only, admin gets full system-wide read access but
zero write override, an ungrouped user gets neither. Locking these in for
real rather than relying on memory of the manual curl verification these
were originally checked with.
"""

import tempfile

import pytest

from app.models.group import Group
from app.models.meeting import ActionItem, Channel, Meeting, MeetingStatus, TranscriptSegment
from app.models.user import UserRole


async def _setup(db, make_user):
    """Alice (grouped) owns a ready meeting with real transcript/action-item
    rows and a real (tiny, temp-file-backed) audio file; Bob shares Alice's
    group; Carol is ungrouped; an admin has no group at all. Returns a dict
    of everything a test needs."""
    group = Group(name="Test Group")
    db.add(group)
    await db.commit()

    alice = await make_user(email="alice@example.com", group_id=group.id)
    bob = await make_user(email="bob@example.com", group_id=group.id)
    carol = await make_user(email="carol@example.com")
    admin = await make_user(email="admin@example.com", role=UserRole.ADMIN)

    audio_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    audio_file.write(b"not-real-audio-bytes-but-a-real-file")
    audio_file.close()

    meeting = Meeting(
        owner_id=alice.id,
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
    # The actual boundary: report-shaped reads are 200, raw content is 404.
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
async def test_ungrouped_user_gets_nothing(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["carol"])

    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 404
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/transcript", headers=headers)
    ).status_code == 404
    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}/audio", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_admin_gets_full_read_access_system_wide(app_client, db, make_user, auth_headers):
    """Admin has no group relation to Alice at all — proving this is a
    real system-wide override, not group visibility in disguise."""
    ctx = await _setup(db, make_user)
    headers = auth_headers(ctx["admin"])

    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}", headers=headers)).status_code == 200
    assert (
        await app_client.get(f"/api/meetings/{ctx['meeting_id']}/transcript", headers=headers)
    ).status_code == 200
    assert (await app_client.get(f"/api/meetings/{ctx['meeting_id']}/audio", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_admin_write_attempts_still_404_read_only_is_enforced(app_client, db, make_user, auth_headers):
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
async def test_all_meetings_listing_is_admin_only(app_client, db, make_user, auth_headers):
    ctx = await _setup(db, make_user)

    admin_response = await app_client.get("/api/meetings/all", headers=auth_headers(ctx["admin"]))
    assert admin_response.status_code == 200
    assert any(m["id"] == ctx["meeting_id"] for m in admin_response.json())

    non_admin_response = await app_client.get("/api/meetings/all", headers=auth_headers(ctx["bob"]))
    assert non_admin_response.status_code == 403
