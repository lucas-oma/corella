from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.user import User


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


async def searchable_kb_keywords(db: AsyncSession, owner_id: UUID) -> list[str]:
    """Every distinct keyword LLM-extracted (app/services/embeddings/
    kb_keywords.py) from a READY KB document `owner_id` can search — same
    owner_ids scope as searchable_owner_ids, just flattened across
    documents, deduped case-insensitively (keeping the first-seen casing),
    and capped, so callers (app/services/asr/*, fed via app/workers/
    tasks.py and app/ws/live_session.py) get one bounded list to pass
    straight to Deepgram's `keywords` param / faster-whisper's
    `initial_prompt`. Pure Postgres — no Qdrant round-trip, since the
    keywords already live on the document row, not just in its chunks.
    """
    owner_ids = await searchable_owner_ids(db, owner_id)
    rows = await db.scalars(
        select(KBDocument.keywords).where(
            KBDocument.owner_id.in_(owner_ids),
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
