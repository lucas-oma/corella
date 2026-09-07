import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost import LLMUsageEvent, UsageKind
from app.services.copilot.json_parse import as_str_list, parse_json_response
from app.services.llm.base import LLMError, LLMMessage, complete
from app.services.llm.pricing import estimate_cost_usd
from app.services.llm.resolve import resolve_provider

logger = logging.getLogger(__name__)

# Good enough for a KB reference doc, same "good enough" bar chunking.py's
# own docstring already sets — a proper-noun/acronym/jargon glossary for a
# document doesn't need its whole text, and this keeps the prompt small and
# cheap regardless of how large the source PDF/doc actually is.
_MAX_INPUT_CHARS = 8000

_SYSTEM_PROMPT = """You extract a short glossary of terms from a knowledge-base document that a speech-to-text engine would likely mishear or mis-transcribe: proper nouns, product/company names, acronyms, and domain-specific jargon. Respond with ONLY a single JSON object, no other text, in exactly this shape:

{
  "keywords": ["<term>", "<term>", ...]
}

Up to 30 terms, each as it's actually spelled/capitalized in the document. Skip common English words and generic terms. Use an empty array if there's genuinely nothing distinctive."""


async def extract_keywords_via_llm(db: AsyncSession, owner_id: UUID, text: str) -> list[str] | None:
    """Best-effort — None on any failure (no LLM provider configured
    anywhere, a request error, malformed JSON back), never raised: called
    from app/workers/tasks.py:process_kb_document, where a keyword-
    extraction miss must never turn an otherwise-successful chunk/embed
    into a FAILED document. Logs its own LLMUsageEvent row (kind=
    KB_EXTRACTION) directly rather than through
    app/services/copilot/cost.py:add_meeting_cost, since this call isn't
    tied to any meeting — that helper's running-total bump assumes a real
    meeting_id.
    """
    provider = await resolve_provider(db, owner_id)
    if provider is None:
        return None

    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=text[:_MAX_INPUT_CHARS]),
    ]

    try:
        response = await complete(
            provider.provider,
            provider.model,
            messages,
            provider.api_key,
            provider.base_url,
            max_tokens=400,
        )
    except LLMError as e:
        logger.info("KB keyword extraction skipped for owner %s: %s", owner_id, e)
        return None

    cost = estimate_cost_usd(
        provider.provider, provider.model, response.input_tokens, response.output_tokens
    )
    db.add(
        LLMUsageEvent(
            meeting_id=None,
            owner_id=owner_id,
            provider=provider.provider.value,
            model=provider.model,
            kind=UsageKind.KB_EXTRACTION,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=cost,
        )
    )
    await db.commit()

    try:
        parsed = parse_json_response(response.text)
    except ValueError as e:
        logger.info("KB keyword extraction skipped for owner %s: %s", owner_id, e)
        return None

    return as_str_list(parsed.get("keywords"))
