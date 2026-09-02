from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.security import encrypt_secret
from app.models.provider_credential import LLMProvider, ProviderCredential
from app.models.stt_credential import SttCredential
from app.models.user import User
from app.services.asr.resolve import resolve_stt_provider
from app.services.llm.resolve import resolve_provider
from app.schemas.settings import (
    AiOverview,
    DiarizationOverview,
    EmbeddingsOverview,
    LanguageModelOverview,
    ProviderCredentialUpdate,
    ProviderStatus,
    SttCredentialUpdate,
    SttOverview,
    SttStatus,
)

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


@router.get("/stt", response_model=SttStatus)
async def stt_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SttStatus:
    credential = await db.scalar(
        select(SttCredential).where(SttCredential.owner_id == current_user.id)
    )
    if credential is not None:
        return SttStatus(connected=True, source="user")
    if get_settings().deepgram_api_key:
        return SttStatus(connected=True, source="env")
    return SttStatus(connected=False, source=None)


@router.put("/stt", response_model=SttStatus)
async def save_stt_credential(
    payload: SttCredentialUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SttStatus:
    credential = await db.scalar(
        select(SttCredential).where(SttCredential.owner_id == current_user.id)
    )
    if credential is None:
        credential = SttCredential(owner_id=current_user.id, api_key_encrypted="")
        db.add(credential)

    credential.api_key_encrypted = encrypt_secret(payload.api_key)
    await db.commit()
    return SttStatus(connected=True, source="user")


@router.delete("/stt", response_model=SttStatus)
async def delete_stt_credential(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SttStatus:
    credential = await db.scalar(
        select(SttCredential).where(SttCredential.owner_id == current_user.id)
    )
    if credential is not None:
        await db.delete(credential)
        await db.commit()

    if get_settings().deepgram_api_key:
        return SttStatus(connected=True, source="env")
    return SttStatus(connected=False, source=None)


@router.get("/ai-overview", response_model=AiOverview)
async def ai_overview(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AiOverview:
    """What's actually powering each part of the app right now, for this
    user — computed from the same resolution functions the app itself
    calls at runtime (resolve_stt_provider/resolve_provider), not
    re-derived guesses. Embeddings/diarization are fixed, not
    user-configurable, so those two rows are just informational.
    """
    settings = get_settings()

    stt = await resolve_stt_provider(db, current_user.id)
    llm = await resolve_provider(db, current_user.id)

    llm_source = None
    if llm is not None:
        has_user_credential = await db.scalar(
            select(ProviderCredential.id).where(
                ProviderCredential.owner_id == current_user.id,
                ProviderCredential.provider == llm.provider,
            )
        )
        llm_source = "user" if has_user_credential else "env"

    return AiOverview(
        speech_to_text=SttOverview(active=stt.provider, model=stt.model, source=stt.source),
        language_model=LanguageModelOverview(
            active=llm.provider if llm else None,
            model=llm.model if llm else None,
            source=llm_source,
        ),
        embeddings=EmbeddingsOverview(model=settings.embedding_model),
        diarization=DiarizationOverview(
            pipeline="pyannote/speaker-diarization-3.1",
            speaker_embedding="pyannote/wespeaker-voxceleb-resnet34-LM",
            available=bool(settings.hf_token),
        ),
    )
