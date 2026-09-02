from app.services.llm.base import LLMError, LLMMessage, LLMResponse


async def complete(
    model: str, messages: list[LLMMessage], api_key: str | None, max_tokens: int
) -> LLMResponse:
    """Uses the official `anthropic` SDK (not raw HTTP) per house convention
    for Claude/Anthropic integrations. `effort: low` since this is called
    from the live copilot loop every ~20s — a fast, cheap structured-output
    task, not a task that benefits from deep reasoning.
    """
    if not api_key:
        raise LLMError("No Anthropic API key configured")

    import anthropic

    system = "\n\n".join(m.content for m in messages if m.role == "system") or None
    turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=turns,
            output_config={"effort": "low"},
        )
    except anthropic.AuthenticationError as e:
        raise LLMError(f"Anthropic authentication failed: {e}") from e
    except anthropic.RateLimitError as e:
        raise LLMError(f"Anthropic rate limited: {e}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Anthropic connection error: {e}") from e

    if response.stop_reason == "refusal":
        raise LLMError("Anthropic declined the request")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    usage = getattr(response, "usage", None)
    return LLMResponse(
        text=text,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
