from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
