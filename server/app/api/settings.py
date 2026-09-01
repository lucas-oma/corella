from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.security import encrypt_secret
from app.models.provider_credential import LLMProvider, ProviderCredential
from app.models.user import User
from app.schemas.settings import ProviderCredentialUpdate, ProviderStatus

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _env_configured(provider: LLMProvider, settings: Settings) -> bool:
    return bool(
        {
            LLMProvider.ANTHROPIC: settings.anthropic_api_key,
            LLMProvider.OPENAI: settings.openai_api_key,
            LLMProvider.GEMINI: settings.gemini_api_key,
            LLMProvider.OLLAMA: settings.ollama_base_url,
        }[provider]
    )


@router.get("/providers", response_model=list[ProviderStatus])
async def provider_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderStatus]:
    settings = get_settings()

    user_configured = set(
        await db.scalars(
            select(ProviderCredential.provider).where(
                ProviderCredential.owner_id == current_user.id
            )
        )
    )

    statuses = []
    for provider in LLMProvider:
        if provider in user_configured:
            statuses.append(ProviderStatus(provider=provider, connected=True, source="user"))
        elif _env_configured(provider, settings):
            statuses.append(ProviderStatus(provider=provider, connected=True, source="env"))
        else:
            statuses.append(ProviderStatus(provider=provider, connected=False, source=None))
    return statuses


@router.put("/providers/{provider}", response_model=ProviderStatus)
async def save_provider_credential(
    provider: LLMProvider,
    payload: ProviderCredentialUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderStatus:
    if provider == LLMProvider.OLLAMA:
        if not payload.base_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="base_url is required for Ollama",
            )
    elif not payload.api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"api_key is required for {provider.value}",
        )

    credential = await db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.owner_id == current_user.id,
            ProviderCredential.provider == provider,
        )
    )
    if credential is None:
        credential = ProviderCredential(owner_id=current_user.id, provider=provider)
        db.add(credential)

    if payload.api_key:
        credential.api_key_encrypted = encrypt_secret(payload.api_key)
    if payload.base_url:
        credential.base_url = payload.base_url

    await db.commit()
    return ProviderStatus(provider=provider, connected=True, source="user")


@router.delete("/providers/{provider}", response_model=ProviderStatus)
async def delete_provider_credential(
    provider: LLMProvider,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderStatus:
    credential = await db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.owner_id == current_user.id,
            ProviderCredential.provider == provider,
        )
    )
    if credential is not None:
        await db.delete(credential)
        await db.commit()

    settings = get_settings()
    if _env_configured(provider, settings):
        return ProviderStatus(provider=provider, connected=True, source="env")
    return ProviderStatus(provider=provider, connected=False, source=None)
