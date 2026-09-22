from app.services.copilot.sentiment import SENTIMENTS, parse_sentiment


def test_parse_sentiment_canonicalizes_case():
    assert parse_sentiment("positive") == "Positive"
    assert parse_sentiment("  ENTHUSIASTIC ") == "Enthusiastic"
    assert parse_sentiment("Neutral") == "Neutral"


def test_parse_sentiment_rejects_unknown_and_mixed():
    assert parse_sentiment("Mixed") is None
    assert parse_sentiment("Cautiously optimistic") is None
    assert parse_sentiment("") is None
    assert parse_sentiment(None) is None
    assert parse_sentiment(72) is None


def test_sentiments_are_the_closed_eight():
    assert SENTIMENTS == (
        "Hostile",
        "Tense",
        "Frustrated",
        "Skeptical",
        "Neutral",
        "Engaged",
        "Positive",
        "Enthusiastic",
    )
