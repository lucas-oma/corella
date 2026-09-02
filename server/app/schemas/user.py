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


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    group_id: UUID | None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthConfig(BaseModel):
    allow_public_registration: bool
