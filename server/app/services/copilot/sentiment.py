"""Closed sentiment vocabulary for live copilot and post-call reports.

Tone (warmth), not outcome — coach_score is how well the call is going
for Me. Unknown / free-text LLM output becomes None rather than Neutral
so analysis is not poisoned by a dump bucket.
"""

SENTIMENTS: tuple[str, ...] = (
    "Hostile",
    "Tense",
    "Frustrated",
    "Skeptical",
    "Neutral",
    "Engaged",
    "Positive",
    "Enthusiastic",
)

_BY_LOWER = {value.lower(): value for value in SENTIMENTS}

# Exact string the live/report prompts quote so the model cannot invent
# "Warm" / "Mixed" / "Receptive".
SENTIMENT_CHOICES = ", ".join(SENTIMENTS)


def parse_sentiment(raw: object) -> str | None:
    """Canonical enum value, or None if missing / not in the closed set."""
    if not isinstance(raw, str):
        return None
    return _BY_LOWER.get(raw.strip().lower())
