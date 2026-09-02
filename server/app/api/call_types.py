from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.call_type import CallType
from app.models.user import User
from app.schemas.call_type import CallTypeOption

router = APIRouter(prefix="/api/call-types", tags=["call-types"])


@router.get("", response_model=list[CallTypeOption])
async def list_call_type_options(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[CallType]:
    """The lightweight, public listing — every authenticated user needs
    this to create a meeting (the Dashboard's call-type picker popup), not
    just admins. See app/api/admin.py's /admin/call-types for the
    full admin-managed CRUD (name/guidance/webhook config)."""
    result = await db.scalars(select(CallType).order_by(CallType.name))
    return list(result)
