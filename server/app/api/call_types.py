from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context_flexible
from app.core.db import get_db
from app.models.call_type import CallType
from app.schemas.call_type import CallTypeOption

router = APIRouter(prefix="/api/call-types", tags=["call-types"])


@router.get("", response_model=list[CallTypeOption])
async def list_call_type_options(
    ctx: AuthContext = Depends(get_auth_context_flexible), db: AsyncSession = Depends(get_db)
) -> list[CallType]:
    """The lightweight listing — every authenticated user (JWT or API key)
    needs this to create a meeting with a non-default type. Scoped to the
    active org (or the API key's org).
    """
    result = await db.scalars(
        select(CallType).where(CallType.organization_id == ctx.org_id).order_by(CallType.name)
    )
    return list(result)
