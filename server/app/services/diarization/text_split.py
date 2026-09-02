def words_in_range(words: list[dict], start_s: float, end_s: float) -> str:
    """Joins the words (each {"word":..., "start":..., "end":...}, seconds,
    from faster-whisper's word_timestamps) whose midpoint falls inside
    [start_s, end_s) into one string — used to attribute the right text to
    a same-room speaker turn once diarize_utterance splits one transcribed
    utterance into several (app/workers/tasks.py). Midpoint rather than a
    stricter full-containment check because a word can legitimately straddle
    a speaker-change boundary by a few tens of milliseconds without either
    person "owning" it more than the other.
    """
    matched = [w for w in words if start_s <= (w["start"] + w["end"]) / 2 < end_s]
    return " ".join(w["word"] for w in matched).strip()
