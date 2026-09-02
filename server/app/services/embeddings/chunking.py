def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Simple character-based chunker with overlap. Good enough for a
    knowledge base of short reference docs; token-aware/semantic chunking
    can replace this later without touching any caller.
    """
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def chunk_transcript(segments: list, chunk_size: int = 800) -> list[tuple[str, int, int]]:
    """Groups *consecutive* transcript segments (already time-ordered, each
    with .text/.start_ms/.end_ms) up to chunk_size chars into one chunk,
    returning (text, start_ms, end_ms) — unlike chunk_text()'s blind
    char-slicing of a flat string, this never splits a segment's own text
    mid-word and keeps every chunk addressable to a real moment in the
    recording, needed for a search result's "jump to this point" link.
    A single segment longer than chunk_size still becomes its own
    (oversized) chunk rather than being cut — same "good enough, not
    precision token-aware chunking" bar as chunk_text().
    """
    chunks: list[tuple[str, int, int]] = []
    current_texts: list[str] = []
    current_len = 0
    current_start: int | None = None
    current_end: int | None = None

    def flush() -> None:
        if current_texts:
            chunks.append((" ".join(current_texts), current_start, current_end))

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if current_texts and current_len + len(text) + 1 > chunk_size:
            flush()
            current_texts, current_len, current_start, current_end = [], 0, None, None
        current_texts.append(text)
        current_len += len(text) + 1
        if current_start is None:
            current_start = seg.start_ms
        current_end = seg.end_ms

    flush()
    return chunks
