from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.models.provider_credential import LLMProvider, ProviderCredential
from app.models.user import User
from app.schemas.settings import ProviderStatus

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
