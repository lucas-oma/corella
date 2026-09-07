"""Best-effort cost estimate for Deepgram STT usage — same spirit and same
"don't fabricate a number for an unpriced case" discipline as
app/services/llm/pricing.py's LLM pricing table.

Verified current pricing (Sept 2026), pay-as-you-go, straight from
https://deepgram.com/pricing — Nova-3 only. Nova-2 (this project's own
`default_model_deepgram` setting, still "nova-2" for backwards
compatibility) is a legacy model Deepgram's own pricing page no longer
lists a rate for directly; third-party aggregators cite a figure, but
it isn't confirmed against Deepgram's own primary source, so it's
deliberately left unpriced here rather than guessed — the same "silently
wrong is worse than honestly unknown" rule the LLM pricing table follows
for an unlisted model. A deployment wanting real Deepgram cost visibility
should set `default_model_deepgram`/a user's `preferred_stt_model` to
"nova-3".

Deepgram prices streaming and pre-recorded separately, and monolingual
vs. multilingual (code-switching) separately — Corella defaults every
user's `preferred_stt_language` to "multi" (Phase R's own fix for the
"silently defaults to English" bug), which is Deepgram's *multilingual*
tier, not monolingual — so the multilingual rate is the one that applies
by default; only a user who's set an explicit single-language preference
gets the monolingual rate.
"""

# (model, "mono"|"multi", is_streaming) -> USD per minute of audio.
_STT_PRICING_PER_MINUTE: dict[tuple[str, str, bool], float] = {
    ("nova-3", "mono", True): 0.0048,
    ("nova-3", "multi", True): 0.0058,
    ("nova-3", "mono", False): 0.0043,
    ("nova-3", "multi", False): 0.0052,
}


def estimate_stt_cost_usd(
    provider: str, model: str, language: str | None, is_streaming: bool, audio_seconds: float | None
) -> float | None:
    """None whenever the inputs don't resolve to a real, known price —
    missing duration, a non-Deepgram provider (local whisper is free —
    self-hosted compute, not a metered API), or a model this table doesn't
    have verified pricing for (see the module docstring re: nova-2)."""
    if provider != "deepgram" or audio_seconds is None:
        return None
    mode = "mono" if language and language != "multi" else "multi"
    rate = _STT_PRICING_PER_MINUTE.get((model, mode, is_streaming))
    if rate is None:
        return None
    return round(audio_seconds / 60 * rate, 6)
