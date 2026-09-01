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
