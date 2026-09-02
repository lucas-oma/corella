import httpx

from app.services.llm.base import LLMError, LLMMessage, LLMResponse

API_URL = "https://api.openai.com/v1/chat/completions"


async def complete(
    model: str, messages: list[LLMMessage], api_key: str | None, max_tokens: int
) -> LLMResponse:
    """Hand-rolled against the Chat Completions REST endpoint rather than
    the official SDK — its long-stable JSON shape is lower-risk for me to
    get right without a live key to verify against than guessing at SDK
    class/method names with no equivalent authoritative reference to the
    `claude-api` skill for this provider.
    """
    if not api_key:
        raise LLMError("No OpenAI API key configured")

    payload = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "max_tokens": max_tokens,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.RequestError as e:
        raise LLMError(f"OpenAI connection error: {e}") from e

    if response.status_code == 401:
        raise LLMError("OpenAI authentication failed")
    if response.status_code == 429:
        raise LLMError("OpenAI rate limited")
    if response.status_code >= 400:
        raise LLMError(f"OpenAI API error ({response.status_code}): {response.text[:500]}")

    try:
        data = response.json()
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as e:
        raise LLMError(f"Unexpected OpenAI response shape: {e}") from e

    usage = data.get("usage") or {}
    return LLMResponse(
        text=text,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
    )
