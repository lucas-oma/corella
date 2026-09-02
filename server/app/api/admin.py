from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_db
from app.core.security import hash_password
from app.models.group import Group
from app.models.user import User
from app.schemas.group import GroupCreate, GroupRead
from app.schemas.user import AdminUserCreate, AdminUserUpdate, UserRead

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
