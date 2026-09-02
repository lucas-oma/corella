from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import ActionItem, ActionItemStatus


async def persist_new_action_items(db: AsyncSession, meeting_id: UUID, texts: list[str]) -> None:
    """Adds any texts not already represented for this meeting — simple
    case-insensitive substring dedup (not fancy NLP dedup, but enough to
    stop the same commitment from being re-added every copilot cycle).
    Caller commits.
    """
    existing = {
        t.lower()
        for t in await db.scalars(select(ActionItem.text).where(ActionItem.meeting_id == meeting_id))
    }
    for text in texts:
        text = text.strip()
        if not text:
            continue
        lowered = text.lower()
        if any(lowered in e or e in lowered for e in existing):
            continue
        db.add(ActionItem(meeting_id=meeting_id, text=text, status=ActionItemStatus.OPEN))
        existing.add(lowered)
