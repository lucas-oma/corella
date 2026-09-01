from pathlib import Path


def extract_text(path: str) -> str:
    """Pull plain text out of a KB source document. Dispatches on extension
    since that's what we validated on upload (see api/kb.py).
    """
    ext = Path(path).suffix.lower()

    if ext == ".pdf":
        from pypdf import PdfReader  # heavy-ish import — deferred to first use

        reader = PdfReader(path)
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)

    # .txt, .md, and anything else we accepted — treat as plain text.
    return Path(path).read_text(encoding="utf-8", errors="replace")
