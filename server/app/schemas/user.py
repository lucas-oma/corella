from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.user import UserRole


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str


class AdminUserCreate(UserCreate):
    role: UserRole = UserRole.MEMBER
    group_id: UUID | None = None


class AdminUserUpdate(BaseModel):
    """Partial update — only fields actually sent are changed. Reassigning
    an existing account's group/role, not creating one (POST .../users)."""

    role: UserRole | None = None
    group_id: UUID | None = None
    clear_group: bool = False  # group_id=None alone is ambiguous with "don't change" — this disambiguates


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ProfileUpdate(BaseModel):
    """Self-service — PATCH /api/auth/me. Distinct from AdminUserUpdate,
    which reassigns role/group on someone *else's* account."""

    full_name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    group_id: UUID | None
    # Not a column — always computed in the route (does a VoiceIdentity row
    # with linked_user_id=self exist) rather than via an ORM relationship,
    # so this schema still round-trips a plain User object via
    # from_attributes everywhere except the one route that fills it in.
    voice_enrolled: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthConfig(BaseModel):
    allow_public_registration: bool
