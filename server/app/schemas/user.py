from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.organization import OrgRole


class OrgMembershipRead(BaseModel):
    id: UUID
    name: str
    role: OrgRole
    is_instance_org: bool


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    is_super_admin: bool
    active_organization_id: UUID | None
    organizations: list[OrgMembershipRead] = []
    group_ids: list[UUID] = []
    voice_enrolled: bool = False


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str


class MemberCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: OrgRole = OrgRole.MEMBER


class MemberUpdate(BaseModel):
    role: OrgRole | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ProfileUpdate(BaseModel):
    full_name: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthConfig(BaseModel):
    allow_public_registration: bool
    max_orgs_per_user: int
    email_invites: bool = False
