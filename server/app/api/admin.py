from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_db
from app.core.security import encrypt_secret, hash_password
from app.models.call_type import CallType
from app.models.group import Group
from app.models.user import User
from app.schemas.call_type import CallTypeCreate, CallTypeRead, CallTypeUpdate
from app.schemas.cost import CostSummaryRead, DailyCostRead, UserCostBreakdownRead
from app.schemas.group import GroupCreate, GroupRead
from app.schemas.user import AdminUserCreate, AdminUserUpdate, UserRead
from app.services.admin.costs import get_cost_summary

router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


@router.get("/users", response_model=list[UserRead])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[User]:
    result = await db.scalars(select(User).order_by(User.created_at))
    return list(result)


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(payload: AdminUserCreate, db: AsyncSession = Depends(get_db)) -> User:
    """Admin-managed account creation — works regardless of the
    ALLOW_PUBLIC_REGISTRATION setting, and is the only way to create an
    account (besides the env-seeded bootstrap admin) once that's off.
    """
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email is already registered"
        )

    if payload.group_id is not None and await db.get(Group, payload.group_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        group_id=payload.group_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID, payload: AdminUserUpdate, db: AsyncSession = Depends(get_db)
) -> User:
    """Reassigns an *existing* account's role and/or group — POST .../users
    is for creating new accounts. group_id=None is ambiguous with "leave it
    alone" for a partial update, hence clear_group to say "actually unset
    it" explicitly.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.role is not None:
        user.role = payload.role
    if payload.clear_group:
        user.group_id = None
    elif payload.group_id is not None:
        if await db.get(Group, payload.group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        user.group_id = payload.group_id

    await db.commit()
    await db.refresh(user)
    return user


@router.get("/groups", response_model=list[GroupRead])
async def list_groups(db: AsyncSession = Depends(get_db)) -> list[GroupRead]:
    result = await db.execute(
        select(Group, func.count(User.id))
        .outerjoin(User, User.group_id == Group.id)
        .group_by(Group.id)
        .order_by(Group.created_at)
    )
    return [
        GroupRead(id=group.id, name=group.name, created_at=group.created_at, member_count=count)
        for group, count in result.all()
    ]


@router.post("/groups", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(payload: GroupCreate, db: AsyncSession = Depends(get_db)) -> GroupRead:
    group = Group(name=payload.name)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return GroupRead(id=group.id, name=group.name, created_at=group.created_at, member_count=0)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    """Unassigns members (User.group_id -> NULL via ondelete=SET NULL) —
    never deletes their accounts."""
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    await db.delete(group)
    await db.commit()


async def _unset_other_defaults(db: AsyncSession, exclude_id: UUID | None) -> None:
    """Enforces the single-default invariant (exactly one CallType with
    is_default=True) in application code, in the same transaction as the
    caller's own insert/update — not a DB constraint, since "exactly one
    true, the rest false" isn't expressible as a simple column check.
    """
    others = await db.scalars(
        select(CallType).where(CallType.is_default.is_(True), CallType.id != exclude_id)
        if exclude_id is not None
        else select(CallType).where(CallType.is_default.is_(True))
    )
    for other in others:
        other.is_default = False


@router.get("/call-types", response_model=list[CallTypeRead])
async def list_call_types(db: AsyncSession = Depends(get_db)) -> list[CallType]:
    result = await db.scalars(select(CallType).order_by(CallType.created_at))
    return list(result)


@router.post("/call-types", response_model=CallTypeRead, status_code=status.HTTP_201_CREATED)
async def create_call_type(payload: CallTypeCreate, db: AsyncSession = Depends(get_db)) -> CallType:
    existing = await db.scalar(select(CallType).where(CallType.slug == payload.slug))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A call type with this slug already exists")

    call_type = CallType(
        name=payload.name,
        slug=payload.slug,
        report_guidance=payload.report_guidance,
        is_default=payload.is_default,
        webhook_enabled=payload.webhook_enabled,
        webhook_url=payload.webhook_url,
        webhook_method=payload.webhook_method,
        webhook_headers_encrypted=encrypt_secret(payload.webhook_headers) if payload.webhook_headers else None,
        webhook_body_template=payload.webhook_body_template,
    )
    db.add(call_type)
    if payload.is_default:
        await db.flush()  # call_type needs its id before excluding it below
        await _unset_other_defaults(db, call_type.id)
    await db.commit()
    await db.refresh(call_type)
    return call_type


@router.patch("/call-types/{call_type_id}", response_model=CallTypeRead)
async def update_call_type(call_type_id: UUID, payload: CallTypeUpdate, db: AsyncSession = Depends(get_db)) -> CallType:
    call_type = await db.get(CallType, call_type_id)
    if call_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call type not found")

    fields = payload.model_dump(exclude_unset=True)
    if "slug" in fields and fields["slug"] != call_type.slug:
        clash = await db.scalar(select(CallType).where(CallType.slug == fields["slug"]))
        if clash is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A call type with this slug already exists")

    if "webhook_headers" in fields:
        headers = fields.pop("webhook_headers")
        call_type.webhook_headers_encrypted = encrypt_secret(headers) if headers else None

    for field, value in fields.items():
        setattr(call_type, field, value)

    if fields.get("is_default"):
        await _unset_other_defaults(db, call_type.id)

    await db.commit()
    await db.refresh(call_type)
    return call_type


@router.delete("/call-types/{call_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_call_type(call_type_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    """Meetings that used this type get call_type_id SET NULL automatically
    (the FK's ondelete rule, app/models/meeting.py) — never blocked or
    cascaded. The one thing that IS blocked: deleting the current default,
    since every new meeting needs a default to resolve to.
    """
    call_type = await db.get(CallType, call_type_id)
    if call_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call type not found")
    if call_type.is_default:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Can't delete the default call type — mark a different one as default first",
        )
    await db.delete(call_type)
    await db.commit()


@router.get("/costs", response_model=CostSummaryRead)
async def get_costs(
    period: Literal["7d", "30d", "month", "year"] = "30d",
    db: AsyncSession = Depends(get_db),
) -> CostSummaryRead:
    """Aggregate LLM cost analytics for the Admin Costs section — total,
    per-user, daily history (dense zero-filled series for `period`), and a
    trailing-average next-7-days projection.
    Built from the LLMUsageEvent ledger (app/services/admin/costs.py), not
    the per-meeting running total, which has no per-event timestamps.
    """
    summary = await get_cost_summary(db, period=period)
    return CostSummaryRead(
        total_usd=summary.total_usd,
        priced_call_count=summary.priced_call_count,
        total_call_count=summary.total_call_count,
        avg_cost_per_call=summary.avg_cost_per_call,
        total_input_tokens=summary.total_input_tokens,
        total_output_tokens=summary.total_output_tokens,
        by_user=[
            UserCostBreakdownRead(
                owner_id=u.owner_id,
                owner_name=u.owner_name,
                total_usd=u.total_usd,
                call_count=u.call_count,
            )
            for u in summary.by_user
        ],
        daily=[DailyCostRead(day=d.day, total_usd=d.total_usd) for d in summary.daily],
        projected_next_7_days_usd=summary.projected_next_7_days_usd,
        period=summary.period,
    )
