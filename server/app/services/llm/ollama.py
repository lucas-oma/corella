import httpx

from app.services.llm.base import LLMError, LLMMessage


async def complete(model: str, messages: list[LLMMessage], base_url: str | None, max_tokens: int) -> str:
    """Hand-rolled against Ollama's /api/chat REST endpoint — this is the
    one provider client verified against a real running instance (see
    Phase E verification), not just documented-shape review.
    """
    if not base_url:
        raise LLMError("No Ollama host configured")

    payload = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": False,
        "options": {"num_predict": max_tokens},
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
    except httpx.RequestError as e:
        raise LLMError(f"Ollama connection error: {e}") from e

    if response.status_code == 404:
        raise LLMError(f"Ollama model not found: {model} (pull it first with `ollama pull {model}`)")
    if response.status_code >= 400:
        raise LLMError(f"Ollama API error ({response.status_code}): {response.text[:500]}")

    try:
        data = response.json()
        return data["message"]["content"].strip()
    except (KeyError, ValueError) as e:
        raise LLMError(f"Unexpected Ollama response shape: {e}") from e
