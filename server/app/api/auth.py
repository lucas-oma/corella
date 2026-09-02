import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import storage
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User, UserRole
from app.models.voice_identity import VoiceIdentity
from app.schemas.user import AuthConfig, ProfileUpdate, Token, UserCreate, UserLogin, UserRead
from app.services.embeddings.qdrant_store import delete_speaker_embedding
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_ALLOWED_VOICE_EXTENSIONS = {".wav", ".webm", ".m4a", ".mp3", ".ogg", ".flac"}


def _looks_like_audio(filename: str | None, content_type: str | None) -> bool:
    """Same permissive check as api/meetings.py's — browsers are
    inconsistent about Content-Type for recorded audio."""
    if Path(filename or "").suffix.lower() in _ALLOWED_VOICE_EXTENSIONS:
        return True
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct.startswith("audio/") or ct == "video/webm"


async def _user_read(db: AsyncSession, user: User) -> UserRead:
    """voice_enrolled isn't a User column — computed here rather than via
    an ORM relationship, since a user has at most one VoiceIdentity
    (linked_user_id=self) and this is the only place that needs it."""
    has_voice = await db.scalar(
        select(VoiceIdentity.id).where(VoiceIdentity.linked_user_id == user.id).limit(1)
    )
    return UserRead.model_validate(user, from_attributes=True).model_copy(
        update={"voice_enrolled": has_voice is not None}
    )


@router.get("/config", response_model=AuthConfig)
async def auth_config() -> AuthConfig:
    """Public, unauthenticated — lets the frontend know whether to offer
    self-serve registration or point people at an admin instead."""
    return AuthConfig(allow_public_registration=get_settings().allow_public_registration)


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> Token:
    if not get_settings().allow_public_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-serve registration is disabled on this instance. Ask an admin to create your account.",
        )

    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email is already registered"
        )

    # Self-serve registration always creates a regular member — admins are
    # provisioned via ADMIN_EMAIL/ADMIN_PASSWORD (see app.core.bootstrap) or
    # created by an existing admin through POST /api/admin/users.
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=UserRole.MEMBER,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return Token(access_token=create_access_token(user.id))


@router.post("/login", response_model=Token)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> Token:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )

    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserRead)
async def me(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> UserRead:
    return await _user_read(db, current_user)


@router.patch("/me", response_model=UserRead)
async def update_me(
    payload: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    current_user.full_name = payload.full_name
    await db.commit()
    await db.refresh(current_user)
    return await _user_read(db, current_user)


@router.post("/me/voice", response_model=UserRead, status_code=status.HTTP_202_ACCEPTED)
async def enroll_voice(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    """Saves a short voice sample and dispatches extraction to the worker
    (torch-dependent, same lean-api/heavy-worker split as every other
    embedding/diarization path) — 202, not 200: voice_enrolled on the
    returned object still reflects the *pre*-enrollment state, since
    extraction hasn't run yet. The frontend polls GET /api/auth/me the
    same way meeting processing status is already polled.
    """
    if not _looks_like_audio(file.filename, file.content_type):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Doesn't look like an audio file: {file.filename} ({file.content_type})",
        )

    await storage.save_voice_upload(current_user.id, file)

    try:
        celery_app.send_task("corella.enroll_voice", args=[str(current_user.id)])
    except Exception:
        logger.exception("Failed to dispatch enroll_voice for user %s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not start voice processing — the background worker is unreachable.",
        ) from None

    return await _user_read(db, current_user)


@router.delete("/me/voice", response_model=UserRead)
async def remove_voice_enrollment(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> UserRead:
    identity = await db.scalar(
        select(VoiceIdentity).where(VoiceIdentity.linked_user_id == current_user.id)
    )
    if identity is not None:
        await db.delete(identity)
        await db.commit()
        delete_speaker_embedding(identity.id)
    storage.delete_voice_sample_files(current_user.id)
    return await _user_read(db, current_user)
