from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.organization import OrgRole


class OrganizationCreate(BaseModel):
    name: str


class OrganizationUpdate(BaseModel):
    name: str


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_instance_org: bool
    role: OrgRole
    created_at: datetime


class CurrentOrgUpdate(BaseModel):
    organization_id: UUID


class TransferOwnership(BaseModel):
    user_id: UUID


class InviteCreate(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.MEMBER


class InviteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    role: OrgRole
    expires_at: datetime
    created_at: datetime
    token: str | None = None


class InvitePreview(BaseModel):
    organization_name: str
    email: EmailStr
    role: OrgRole
    expires_at: datetime


class InviteAccept(BaseModel):
    password: str | None = None
    full_name: str | None = None


class MemberRead(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str
    role: OrgRole
    group_ids: list[UUID]
    is_super_admin: bool = False


class GroupMemberUpdate(BaseModel):
    user_ids: list[UUID]
