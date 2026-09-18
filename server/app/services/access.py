from uuid import UUID

from sqlalchemy import and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.core.config import get_settings
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.user import User, UserRole


async def searchable_owner_ids(db: AsyncSession, owner_id: UUID) -> list[UUID]:
    """Whose knowledge-base documents (and, once cross-meeting voice
    recognition exists, speaker embeddings) `owner_id` can search: just
    themselves if ungrouped — the default, and the whole system's behavior
    before groups existed — or every member of their group if they have
    one. Deliberately NOT used for meeting search/transcripts — those stay
    strictly per-owner regardless of group membership. Takes the id (not a
    loaded User) since every call site already has that and nothing else
    about the user.
    """
    group_id = await db.scalar(select(User.group_id).where(User.id == owner_id))
    if group_id is None:
        return [owner_id]
    member_ids = await db.scalars(select(User.id).where(User.group_id == group_id))
    return list(member_ids)


def kb_visible_clause(user: User, *, admin_sees_all: bool = False) -> ColumnElement[bool]:
    """Which kb_documents this user may list / copilot-search.

    A grouped user sees documents assigned to their group, plus any
    unassigned docs they themselves uploaded (legacy). An ungrouped user
    sees only their own unassigned docs. Admins are the same for copilot
    and STT keywords (so one admin meeting doesn't pull every group's
    KB). The documents list passes admin_sees_all=True so they can
    maintain every group from /knowledge-base.
    """
    if admin_sees_all and user.role == UserRole.ADMIN:
        return true()
    if user.group_id is not None:
        return or_(
            KBDocument.group_id == user.group_id,
            and_(KBDocument.group_id.is_(None), KBDocument.owner_id == user.id),
        )
    return and_(KBDocument.group_id.is_(None), KBDocument.owner_id == user.id)


async def searchable_kb_keywords(db: AsyncSession, owner_id: UUID) -> list[str]:
    """Every distinct keyword LLM-extracted (app/services/embeddings/
    kb_keywords.py) from a READY KB document `owner_id` can search — same
    visibility as copilot (kb_visible_clause), flattened across
    documents, deduped case-insensitively (keeping the first-seen casing),
    and capped, so callers (app/services/asr/*, fed via app/workers/
    tasks.py and app/ws/live_session.py) get one bounded list to pass
    straight to Deepgram's `keywords` param / faster-whisper's
    `initial_prompt`. Pure Postgres — no Qdrant round-trip, since the
    keywords already live on the document row, not just in its chunks.
    """
    user = await db.get(User, owner_id)
    if user is None:
        return []
    rows = await db.scalars(
        select(KBDocument.keywords).where(
            kb_visible_clause(user),
            KBDocument.status == KBDocumentStatus.READY,
            KBDocument.keywords.is_not(None),
        )
    )
    limit = get_settings().stt_keyword_limit
    seen: dict[str, str] = {}
    for keywords in rows:
        for keyword in keywords:
            key = keyword.lower()
            if key not in seen:
                seen[key] = keyword
                if len(seen) >= limit:
                    return list(seen.values())
    return list(seen.values())
