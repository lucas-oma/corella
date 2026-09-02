import json
import re


def parse_json_response(text: str) -> dict:
    """LLMs often wrap JSON in a markdown code fence even when asked not
    to — strip that before parsing. Raises json.JSONDecodeError on
    genuinely malformed output; callers should catch it and skip the
    cycle/section rather than crash.
    """
    stripped = text.strip()
    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```$", stripped, re.DOTALL)
    if match:
        stripped = match.group(1).strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
    return parsed


def as_str_list(value: object) -> list[str]:
    """Coerce a parsed JSON field expected to be a string array.

    Valid JSON doesn't guarantee the right *shape* — a model can return
    `"action_items": "call them back"` (a string, not an array). Iterating
    that directly explodes it into one "item" per character, so anything
    that isn't actually a list is treated as empty rather than trusted.
    """
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if x]
