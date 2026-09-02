from dataclasses import dataclass

from app.models.provider_credential import LLMProvider


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    text: str
    input_tokens: int | None
    output_tokens: int | None


class LLMError(Exception):
    """Raised by any provider client on a request failure — auth, rate
    limit, network, or an unexpected response shape. Callers (the live
    copilot loop, report generation) catch this uniformly rather than each
    provider's own exception types.
    """


async def complete(
    provider: LLMProvider,
    model: str,
    messages: list[LLMMessage],
    api_key: str | None,
    base_url: str | None,
    max_tokens: int = 1024,
) -> LLMResponse:
    """Dispatch a non-streaming completion to the right provider client.
    Returns the response text plus token usage (for cost tracking, see
    app/services/llm/pricing.py — either count can be None if the
    provider's response didn't include it), or raises LLMError.
    """
    if provider == LLMProvider.ANTHROPIC:
        from app.services.llm.anthropic import complete as anthropic_complete

        return await anthropic_complete(model, messages, api_key, max_tokens)
    if provider == LLMProvider.OPENAI:
        from app.services.llm.openai import complete as openai_complete

        return await openai_complete(model, messages, api_key, max_tokens)
    if provider == LLMProvider.GEMINI:
        from app.services.llm.gemini import complete as gemini_complete

        return await gemini_complete(model, messages, api_key, max_tokens)
    if provider == LLMProvider.OLLAMA:
        from app.services.llm.ollama import complete as ollama_complete

        return await ollama_complete(model, messages, base_url, max_tokens)
    raise LLMError(f"Unsupported provider: {provider}")
