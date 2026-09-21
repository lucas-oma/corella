from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import ActionItem, ActionItemSource, ActionItemStatus


async def persist_new_action_items(db: AsyncSession, meeting_id: UUID, texts: list[str]) -> None:
    """Adds any texts not already represented as a *live* capture for this
    meeting — simple case-insensitive substring dedup (not fancy NLP, but
    enough to stop the same commitment from being re-added every copilot
    cycle). Report digest rows are ignored so a later report cannot block
    live capture. Caller commits.
    """
    existing = {
        t.lower()
        for t in await db.scalars(
            select(ActionItem.text).where(
                ActionItem.meeting_id == meeting_id, ActionItem.source == ActionItemSource.LIVE
            )
        )
    }
    for text in texts:
        text = text.strip()
        if not text:
            continue
        lowered = text.lower()
        if any(lowered in e or e in lowered for e in existing):
            continue
        db.add(
            ActionItem(
                meeting_id=meeting_id,
                text=text,
                status=ActionItemStatus.OPEN,
                source=ActionItemSource.LIVE,
            )
        )
        existing.add(lowered)


async def replace_report_action_items(
    db: AsyncSession, meeting_id: UUID, texts: list[str]
) -> list[ActionItem]:
    """Drop the previous report digest and write a new one. Live captures
    are left alone. Dedupes the incoming list on case-insensitive exact
    match so the model repeating a line twice does not create two rows.
    Caller commits.
    """
    previous = list(
        await db.scalars(
            select(ActionItem).where(
                ActionItem.meeting_id == meeting_id, ActionItem.source == ActionItemSource.REPORT
            )
        )
    )
    for item in previous:
        await db.delete(item)

    created: list[ActionItem] = []
    seen: set[str] = set()
    for text in texts:
        text = text.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        item = ActionItem(
            meeting_id=meeting_id,
            text=text,
            status=ActionItemStatus.OPEN,
            source=ActionItemSource.REPORT,
        )
        db.add(item)
        created.append(item)
    await db.flush()
    return created


async def list_report_action_items(db: AsyncSession, meeting_id: UUID) -> list[ActionItem]:
    return list(
        await db.scalars(
            select(ActionItem)
            .where(ActionItem.meeting_id == meeting_id, ActionItem.source == ActionItemSource.REPORT)
            .order_by(ActionItem.created_at)
        )
    )
