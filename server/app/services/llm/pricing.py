from app.models.provider_credential import LLMProvider

# USD per 1M tokens, (input, output). Point-in-time table (checked September
# 2026) covering only the models actually reachable today — there's no
# per-user/per-call model-choice UI yet, resolve.py's _default_model()
# always returns one fixed instance-wide default per provider, so exactly
# these three cloud models are all that need real numbers. A model outside
# this table (someone changes DEFAULT_MODEL_* to something not listed) is
# left unpriced rather than guessed — needs manual upkeep as pricing changes.
_PRICING: dict[tuple[LLMProvider, str], tuple[float, float]] = {
    (LLMProvider.ANTHROPIC, "claude-sonnet-5"): (2.00, 10.00),
    (LLMProvider.OPENAI, "gpt-4o-mini"): (0.15, 0.60),
    (LLMProvider.GEMINI, "gemini-3.6-flash"): (0.75, 3.75),
}


def estimate_cost_usd(
    provider: LLMProvider, model: str, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    """Best-effort cost estimate for one completion. Ollama is always free
    (local compute) regardless of model. Returns None — not 0 — for any
    other unpriced model or missing token counts, so an unknown cost never
    silently reads as "free."
    """
    if provider == LLMProvider.OLLAMA:
        return 0.0
    if input_tokens is None or output_tokens is None:
        return None
    prices = _PRICING.get((provider, model))
    if prices is None:
        return None
    price_in, price_out = prices
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out
