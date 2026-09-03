"""app/services/llm/pricing.py — pure function, no DB needed."""

from app.models.provider_credential import LLMProvider
from app.services.llm.pricing import estimate_cost_usd


def test_known_model_hand_computed():
    # 1,000,000 input + 500,000 output tokens against Anthropic's $2/$10
    # per-1M table entry: 1.0 * 2.00 + 0.5 * 10.00 = 7.00 exactly.
    cost = estimate_cost_usd(LLMProvider.ANTHROPIC, "claude-sonnet-5", 1_000_000, 500_000)
    assert cost == 7.00


def test_openai_known_model():
    # 200,000 input + 100,000 output against $0.15/$0.60 per 1M:
    # 0.2 * 0.15 + 0.1 * 0.60 = 0.03 + 0.06 = 0.09
    cost = estimate_cost_usd(LLMProvider.OPENAI, "gpt-4o-mini", 200_000, 100_000)
    assert round(cost, 6) == 0.09


def test_gemini_known_model():
    # 400,000 input + 200,000 output against $0.75/$3.75 per 1M:
    # 0.4 * 0.75 + 0.2 * 3.75 = 0.30 + 0.75 = 1.05
    cost = estimate_cost_usd(LLMProvider.GEMINI, "gemini-3.6-flash", 400_000, 200_000)
    assert round(cost, 6) == 1.05


def test_ollama_always_free_regardless_of_tokens_or_model():
    assert estimate_cost_usd(LLMProvider.OLLAMA, "literally-anything", 10_000_000, 10_000_000) == 0.0
    assert estimate_cost_usd(LLMProvider.OLLAMA, "llama3.2", None, None) == 0.0


def test_unknown_model_returns_none_not_a_fabricated_number():
    assert estimate_cost_usd(LLMProvider.ANTHROPIC, "some-future-model-not-in-the-table", 1000, 1000) is None


def test_missing_token_counts_returns_none():
    assert estimate_cost_usd(LLMProvider.ANTHROPIC, "claude-sonnet-5", None, 500) is None
    assert estimate_cost_usd(LLMProvider.ANTHROPIC, "claude-sonnet-5", 500, None) is None
