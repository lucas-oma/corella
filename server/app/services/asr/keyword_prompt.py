_MAX_TERMS = 30


def keywords_to_initial_prompt(keywords: list[str]) -> str | None:
    """Turns a KB keyword list (app/services/access.py:searchable_kb_keywords)
    into faster-whisper's `initial_prompt` — a short string that conditions
    the decoder before it transcribes, biasing it toward these terms the
    same way Deepgram's `keywords` param does (app/services/asr/
    deepgram.py). Capped well below Deepgram's own list — this is prose
    fed into the model's limited decoding context, not a structured param,
    so a long comma-joined list would just crowd out the audio itself.
    None (not "") for an empty list — every call site treats that as
    "nothing to pass", same convention as the `keywords` param elsewhere.
    """
    if not keywords:
        return None
    return "Pay attention to these terms if they're mentioned: " + ", ".join(keywords[:_MAX_TERMS])
