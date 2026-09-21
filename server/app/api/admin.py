import json
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_org_admin, require_super_admin
from app.core.db import get_db
from app.core.security import decrypt_secret, encrypt_secret
from app.models.app_secret import AppSecret
from app.models.call_type import CallType
from app.models.organization import Organization, OrganizationMembership, OrgRole
from app.models.user import User
from app.schemas.app_secret import AppSecretCreate, AppSecretRead, AppSecretUpdate
from app.schemas.call_type import CallTypeCreate, CallTypeRead, CallTypeUpdate
from app.schemas.cost import CostSummaryRead, DailyCostRead, ProviderCostBreakdownRead, UserCostBreakdownRead
from app.schemas.organization import OrganizationRead
from app.services.admin.costs import get_cost_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/organizations", response_model=list[OrganizationRead])
async def list_all_organizations(
    _admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> list[OrganizationRead]:
    rows = (
        await db.execute(
            select(Organization, OrganizationMembership.role)
            .join(
                OrganizationMembership,
                (OrganizationMembership.organization_id == Organization.id)
                & (OrganizationMembership.role == OrgRole.OWNER),
            )
            .order_by(Organization.created_at)
        )
    ).all()
    return [
        OrganizationRead(
            id=org.id,
            name=org.name,
            is_instance_org=org.is_instance_org,
            role=role,
            created_at=org.created_at,
        )
        for org, role in rows
    ]





class SuperAdminFlag(BaseModel):
    is_super_admin: bool


@router.patch("/users/{user_id}/super-admin")
async def set_super_admin(
    user_id: UUID,
    payload: SuperAdminFlag,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_user.id and not payload.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot remove your own super-admin flag",
        )
    user.is_super_admin = payload.is_super_admin
    await db.commit()
    return {"id": str(user.id), "is_super_admin": user.is_super_admin}


@router.get("/users")
async def list_instance_users(
    _admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    users = list(await db.scalars(select(User).order_by(User.created_at)))
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "full_name": u.full_name,
            "is_super_admin": u.is_super_admin,
        }
        for u in users
    ]


async def _unset_other_defaults(
    db: AsyncSession, organization_id: UUID, exclude_id: UUID | None
) -> None:
    """Enforces the single-default invariant per org."""
    others = await db.scalars(
        select(CallType).where(
            CallType.organization_id == organization_id,
            CallType.is_default.is_(True),
            CallType.id != exclude_id,
        )
        if exclude_id is not None
        else select(CallType).where(
            CallType.organization_id == organization_id, CallType.is_default.is_(True)
        )
    )
    for other in others:
        other.is_default = False


def _decrypt_headers(encrypted: str | None) -> str | None:
    if not encrypted:
        return None
    try:
        return decrypt_secret(encrypted)
    except Exception:
        logger.exception("Failed to decrypt call-type headers")
        return None


def _headers_for_storage(raw: str | None) -> str | None:
    """Validate admin-authored header JSON (object) and encrypt the
    template as written. Empty/None clears. Values should be
    {{secret.NAME}} refs; literal tokens still work but will be visible
    to admins on the next GET."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Headers must be valid JSON: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Headers must be a JSON object",
        )
    return encrypt_secret(text)


def _to_call_type_read(call_type: CallType) -> CallTypeRead:
    return CallTypeRead.model_validate(call_type).model_copy(
        update={
            "pre_call_headers": _decrypt_headers(call_type.pre_call_headers_encrypted),
            "post_call_headers": _decrypt_headers(call_type.post_call_headers_encrypted),
        }
    )


async def _require_unique_secret_name(
    db: AsyncSession, organization_id: UUID, name: str, exclude_id: UUID | None = None
) -> None:
    query = select(AppSecret).where(
        AppSecret.organization_id == organization_id, AppSecret.name == name
    )
    if exclude_id is not None:
        query = query.where(AppSecret.id != exclude_id)
    existing = await db.scalar(query)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A secret with this name already exists",
        )


@router.get("/secrets", response_model=list[AppSecretRead])
async def list_secrets(
    ctx: AuthContext = Depends(require_org_admin), db: AsyncSession = Depends(get_db)
) -> list[AppSecret]:
    result = await db.scalars(
        select(AppSecret).where(AppSecret.organization_id == ctx.org_id).order_by(AppSecret.name)
    )
    return list(result)


@router.post("/secrets", response_model=AppSecretRead, status_code=status.HTTP_201_CREATED)
async def create_secret(
    payload: AppSecretCreate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> AppSecret:
    await _require_unique_secret_name(db, ctx.org_id, payload.name)
    secret = AppSecret(
        organization_id=ctx.org_id,
        name=payload.name,
        value_encrypted=encrypt_secret(payload.value),
    )
    db.add(secret)
    await db.commit()
    await db.refresh(secret)
    return secret


@router.patch("/secrets/{secret_id}", response_model=AppSecretRead)
async def update_secret(
    secret_id: UUID,
    payload: AppSecretUpdate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> AppSecret:
    secret = await db.get(AppSecret, secret_id)
    if secret is None or secret.organization_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found")

    if "name" in payload.model_fields_set and payload.name is not None and payload.name != secret.name:
        await _require_unique_secret_name(db, ctx.org_id, payload.name, exclude_id=secret.id)
        secret.name = payload.name
    if "value" in payload.model_fields_set and payload.value is not None:
        secret.value_encrypted = encrypt_secret(payload.value)

    await db.commit()
    await db.refresh(secret)
    return secret


@router.delete("/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_id: UUID,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    secret = await db.get(AppSecret, secret_id)
    if secret is None or secret.organization_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found")
    await db.delete(secret)
    await db.commit()


@router.get("/call-types", response_model=list[CallTypeRead])
async def list_call_types(
    ctx: AuthContext = Depends(require_org_admin), db: AsyncSession = Depends(get_db)
) -> list[CallTypeRead]:
    result = await db.scalars(
        select(CallType).where(CallType.organization_id == ctx.org_id).order_by(CallType.created_at)
    )
    return [_to_call_type_read(ct) for ct in result]


@router.post("/call-types", response_model=CallTypeRead, status_code=status.HTTP_201_CREATED)
async def create_call_type(
    payload: CallTypeCreate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> CallTypeRead:
    existing = await db.scalar(
        select(CallType).where(CallType.organization_id == ctx.org_id, CallType.slug == payload.slug)
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A call type with this slug already exists")

    call_type = CallType(
        organization_id=ctx.org_id,
        name=payload.name,
        slug=payload.slug,
        report_guidance=payload.report_guidance,
        is_default=payload.is_default,
        pre_call_enabled=payload.pre_call_enabled,
        pre_call_url=payload.pre_call_url,
        pre_call_method=payload.pre_call_method,
        pre_call_headers_encrypted=_headers_for_storage(payload.pre_call_headers),
        pre_call_body_template=payload.pre_call_body_template,
        pre_call_use_as_context=payload.pre_call_use_as_context,
        pre_call_async=payload.pre_call_async,
        post_call_enabled=payload.post_call_enabled,
        post_call_url=payload.post_call_url,
        post_call_method=payload.post_call_method,
        post_call_headers_encrypted=_headers_for_storage(payload.post_call_headers),
        post_call_body_template=payload.post_call_body_template,
        post_call_send_full_payload=payload.post_call_send_full_payload,
        post_call_async=payload.post_call_async,
    )
    db.add(call_type)
    if payload.is_default:
        await db.flush()
        await _unset_other_defaults(db, ctx.org_id, call_type.id)
    await db.commit()
    await db.refresh(call_type)
    return _to_call_type_read(call_type)


@router.patch("/call-types/{call_type_id}", response_model=CallTypeRead)
async def update_call_type(
    call_type_id: UUID,
    payload: CallTypeUpdate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> CallTypeRead:
    call_type = await db.get(CallType, call_type_id)
    if call_type is None or call_type.organization_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call type not found")

    fields = payload.model_dump(exclude_unset=True)
    if "slug" in fields and fields["slug"] != call_type.slug:
        clash = await db.scalar(
            select(CallType).where(
                CallType.organization_id == ctx.org_id, CallType.slug == fields["slug"]
            )
        )
        if clash is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A call type with this slug already exists")

    if "pre_call_headers" in fields:
        call_type.pre_call_headers_encrypted = _headers_for_storage(fields.pop("pre_call_headers"))
    if "post_call_headers" in fields:
        call_type.post_call_headers_encrypted = _headers_for_storage(fields.pop("post_call_headers"))

    for field, value in fields.items():
        setattr(call_type, field, value)

    if fields.get("is_default"):
        await _unset_other_defaults(db, ctx.org_id, call_type.id)

    await db.commit()
    await db.refresh(call_type)
    return _to_call_type_read(call_type)


@router.delete("/call-types/{call_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_call_type(
    call_type_id: UUID,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    call_type = await db.get(CallType, call_type_id)
    if call_type is None or call_type.organization_id != ctx.org_id:
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
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> CostSummaryRead:
    summary = await get_cost_summary(db, period=period, organization_id=ctx.org_id)
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
        by_provider=[
            ProviderCostBreakdownRead(provider=p.provider, total_usd=p.total_usd, call_count=p.call_count)
            for p in summary.by_provider
        ],
        daily=[DailyCostRead(day=d.day, total_usd=d.total_usd) for d in summary.daily],
        projected_next_7_days_usd=summary.projected_next_7_days_usd,
        period=summary.period,
    )


@router.get("/costs/instance", response_model=CostSummaryRead)
async def get_instance_costs(
    period: Literal["7d", "30d", "month", "year"] = "30d",
    _admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> CostSummaryRead:
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
        by_provider=[
            ProviderCostBreakdownRead(provider=p.provider, total_usd=p.total_usd, call_count=p.call_count)
            for p in summary.by_provider
        ],
        daily=[DailyCostRead(day=d.day, total_usd=d.total_usd) for d in summary.daily],
        projected_next_7_days_usd=summary.projected_next_7_days_usd,
        period=summary.period,
    )
