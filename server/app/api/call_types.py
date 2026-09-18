from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_flexible
from app.core.db import get_db
from app.models.call_type import CallType
from app.models.user import User
from app.schemas.call_type import CallTypeOption

router = APIRouter(prefix="/api/call-types", tags=["call-types"])


@router.get("", response_model=list[CallTypeOption])
async def list_call_type_options(
    current_user: User = Depends(get_current_user_flexible), db: AsyncSession = Depends(get_db)
) -> list[CallType]:
    """The lightweight listing — every authenticated user (JWT or API key)
    needs this to create a meeting with a non-default type. Id/name/slug/
    is_default only; hook URLs and secrets stay on the admin CRUD. See
    app/api/admin.py's /admin/call-types for that."""
    result = await db.scalars(select(CallType).order_by(CallType.name))
    return list(result)
