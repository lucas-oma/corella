"""app/services/asr/pricing.py — pure function, no DB needed. Same
"don't fabricate a number" discipline as tests/test_pricing.py's LLM
equivalent."""

from app.services.asr.pricing import estimate_stt_cost_usd


def test_nova3_multilingual_streaming_hand_computed():
    # 120 real seconds (2 minutes) at Deepgram's own $0.0058/min
    # multilingual-streaming rate: 2 * 0.0058 = 0.0116.
    cost = estimate_stt_cost_usd("deepgram", "nova-3", "multi", is_streaming=True, audio_seconds=120)
    assert round(cost, 6) == 0.0116


def test_nova3_monolingual_prerecorded_hand_computed():
    # 90 real seconds (1.5 minutes) at Deepgram's own $0.0043/min
    # monolingual-prerecorded rate: 1.5 * 0.0043 = 0.00645.
    cost = estimate_stt_cost_usd("deepgram", "nova-3", "es", is_streaming=False, audio_seconds=90)
    assert round(cost, 6) == 0.00645


def test_multi_language_selects_multilingual_rate_not_mono():
    streaming_multi = estimate_stt_cost_usd("deepgram", "nova-3", "multi", is_streaming=True, audio_seconds=60)
    streaming_mono = estimate_stt_cost_usd("deepgram", "nova-3", "en", is_streaming=True, audio_seconds=60)
    assert streaming_multi != streaming_mono
    assert streaming_multi == 0.0058
    assert streaming_mono == 0.0048


def test_none_language_treated_as_multi_default():
    # Matches ResolvedStt's own "multi" default (Phase R) — a caller that
    # somehow has no language value shouldn't silently price at the wrong
    # (monolingual) rate.
    assert estimate_stt_cost_usd(
        "deepgram", "nova-3", None, is_streaming=True, audio_seconds=60
    ) == estimate_stt_cost_usd("deepgram", "nova-3", "multi", is_streaming=True, audio_seconds=60)


def test_non_deepgram_provider_returns_none():
    # Local whisper is free self-hosted compute, not a metered API — never
    # a fabricated number.
    assert estimate_stt_cost_usd("whisper", "small", "multi", is_streaming=False, audio_seconds=100) is None


def test_missing_audio_seconds_returns_none():
    assert estimate_stt_cost_usd("deepgram", "nova-3", "multi", is_streaming=True, audio_seconds=None) is None


def test_unpriced_model_returns_none_not_a_fabricated_number():
    # nova-2 deliberately has no verified pricing (see pricing.py's own
    # module docstring) — must not guess.
    assert estimate_stt_cost_usd("deepgram", "nova-2", "multi", is_streaming=True, audio_seconds=60) is None
