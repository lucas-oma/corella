import httpx

from app.services.llm.base import LLMError, LLMMessage

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Gemini's REST API uses "model" rather than "assistant" for the model's own turns.
_ROLE_MAP = {"user": "user", "assistant": "model"}


async def complete(model: str, messages: list[LLMMessage], api_key: str | None, max_tokens: int) -> str:
    """Hand-rolled against the generateContent REST endpoint — same
    rationale as openai.py: a stable, well-documented JSON shape I can
    implement confidently without a live key to verify SDK specifics against.
    """
    if not api_key:
        raise LLMError("No Gemini API key configured")

    system_text = "\n\n".join(m.content for m in messages if m.role == "system")
    contents = [
        {"role": _ROLE_MAP.get(m.role, "user"), "parts": [{"text": m.content}]}
        for m in messages
        if m.role != "system"
    ]

    payload: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            # Gemini 3 models "think" before answering by default, spending
            # part of maxOutputTokens on hidden reasoning tokens — verified
            # live: with a tight budget (e.g. the live copilot's 220) this
            # can consume the whole budget and leave nothing for the actual
            # answer (finishReason MAX_TOKENS, no `parts` in the response).
            # thinkingBudget=1 is the minimum that still guarantees real
            # output — same intent as anthropic.py's effort="low": fast,
            # cheap, predictable for a high-volume, latency-sensitive route.
            "thinkingConfig": {"thinkingBudget": 1},
        },
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                API_URL.format(model=model),
                headers={"x-goog-api-key": api_key},
                json=payload,
            )
    except httpx.RequestError as e:
        raise LLMError(f"Gemini connection error: {e}") from e

    if response.status_code in (401, 403):
        raise LLMError("Gemini authentication failed")
    if response.status_code == 429:
        raise LLMError("Gemini rate limited")
    if response.status_code >= 400:
        raise LLMError(f"Gemini API error ({response.status_code}): {response.text[:500]}")

    try:
        data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError) as e:
        raise LLMError(f"Unexpected Gemini response shape: {e}") from e
