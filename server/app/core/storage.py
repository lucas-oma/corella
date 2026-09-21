import mimetypes
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from app.core.config import get_settings

# Upload filenames only contribute an extension, glued onto "original".
# Restrict that suffix so a Content-Disposition value cannot inject extra
# path segments into dest_path (CodeQL py/path-injection on _save_upload).
_SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")

CHUNK_SIZE = 1024 * 1024  # 1MB

# mimetypes.guess_type() depends on the OS's mime database, which varies by
# distro/image (the slim container image this runs in lacks the extended
# table macOS/Ubuntu ship, and guesses "application/octet-stream" for e.g.
# .m4a — a type an <audio> element won't reliably play). Register the
# extensions we actually accept (_looks_like_audio in api/meetings.py)
# explicitly so playback content-type is consistent everywhere.
for _ext, _type in {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".caf": "audio/x-caf",
}.items():
    mimetypes.add_type(_type, _ext)


def _contained_under(root: Path, candidate: Path) -> Path:
    """Resolve candidate and refuse it if it would sit outside root.

    UUID-keyed dirs and original.{ext} writes are already under root by
    construction; this is the explicit check CodeQL's path-injection query
    needs to see, and a last line of defense if a caller ever passed a
    weird id.
    """
    base = root.resolve()
    resolved = candidate.resolve()
    # is_relative_to is the readable check; commonpath is the shape CodeQL's
    # py/path-injection sanitizer recognizes.
    if (
        not resolved.is_relative_to(base)
        or os.path.commonpath((str(base), str(resolved))) != str(base)
    ):
        raise ValueError("storage path escapes root")
    return resolved


def _contained_path(root: str, *parts: str) -> Path:
    base = Path(root)
    return _contained_under(base, base.joinpath(*parts))


def _item_dir(root: str, item_id: UUID) -> Path:
    return _contained_path(root, str(item_id))


def _safe_upload_suffix(filename: str | None) -> str:
    """The only piece of an upload filename we persist on disk: a dotted
    alphanumeric extension, taken from the last path component. Anything
    else (empty, traversal-shaped, double-dot) becomes .bin — same
    fallback _save_upload already used for a missing suffix.
    """
    suffix = Path(Path(filename or "").name).suffix.lower()
    matched = _SAFE_SUFFIX.fullmatch(suffix)
    if matched is None:
        return ".bin"
    return matched.group(0)


async def _save_upload(dest_dir: Path, upload: UploadFile, max_mb: int) -> str:
    """Stream an uploaded file to disk, enforcing a size cap rather than
    buffering the whole thing in memory. Returns the absolute path it was
    written to.
    """
    max_bytes = max_mb * 1024 * 1024

    dest_dir = dest_dir.resolve()
    ext = _safe_upload_suffix(upload.filename)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = _contained_under(dest_dir, dest_dir / f"original{ext}")

    written = 0
    try:
        with open(dest_path, "wb") as f:
            while chunk := await upload.read(CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds the {max_mb}MB limit",
                    )
                f.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    if written == 0:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    return str(dest_path)


# --- Meeting audio -----------------------------------------------------


def meeting_dir(meeting_id: UUID) -> Path:
    return _item_dir(get_settings().audio_storage_path, meeting_id)


async def save_upload(meeting_id: UUID, upload: UploadFile) -> str:
    settings = get_settings()
    return await _save_upload(meeting_dir(meeting_id), upload, settings.max_audio_upload_mb)


def delete_meeting_files(meeting_id: UUID) -> None:
    """Best-effort cleanup of a meeting's stored audio when the meeting
    itself is deleted. Never raises — a missing/already-gone directory is
    not an error here.
    """
    try:
        shutil.rmtree(meeting_dir(meeting_id), ignore_errors=True)
    except ValueError:
        return


# --- Knowledge base documents -------------------------------------------


def kb_document_dir(document_id: UUID) -> Path:
    return _item_dir(get_settings().kb_storage_path, document_id)


async def save_kb_upload(document_id: UUID, upload: UploadFile) -> str:
    settings = get_settings()
    return await _save_upload(kb_document_dir(document_id), upload, settings.max_kb_upload_mb)


def delete_kb_document_files(document_id: UUID) -> None:
    """Best-effort cleanup of a KB document's stored file. Never raises."""
    try:
        shutil.rmtree(kb_document_dir(document_id), ignore_errors=True)
    except ValueError:
        return


# --- Profile voice enrollment (Phase O) ---------------------------------
# Shares the audio_data volume/root rather than a new setting/mount — a
# voice sample is a few seconds of audio, no different in kind from
# meeting recordings, just under a separate "voice_samples" subpath so it
# never collides with a real UUID-keyed meeting directory.


def voice_sample_dir(user_id: UUID) -> Path:
    return _contained_path(get_settings().audio_storage_path, "voice_samples", str(user_id))


async def save_voice_upload(user_id: UUID, upload: UploadFile) -> str:
    settings = get_settings()
    return await _save_upload(voice_sample_dir(user_id), upload, settings.max_audio_upload_mb)


def delete_voice_sample_files(user_id: UUID) -> None:
    """Best-effort cleanup of a user's stored voice sample. Never raises."""
    try:
        shutil.rmtree(voice_sample_dir(user_id), ignore_errors=True)
    except ValueError:
        return


def find_voice_sample_path(user_id: UUID) -> str | None:
    """The API route saves under a variable extension (original.wav,
    original.webm, ...) without recording it anywhere else — the worker
    task (corella.enroll_voice) locates it by globbing this same
    convention, same "original.<ext>" pattern _save_upload always writes.
    """
    try:
        matches = sorted(voice_sample_dir(user_id).glob("original.*"))
    except ValueError:
        return None
    return str(matches[0]) if matches else None


# --- Range-aware file serving (meeting audio playback) ------------------


def _iter_file(path: Path, start: int, end: int, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def range_response(file_path: str, range_header: str | None) -> Response:
    """Serve a file honoring an HTTP Range header (206 Partial Content) so
    an <audio> element can seek without downloading the whole recording.
    """
    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio not found")

    file_size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    if range_header is None:
        return StreamingResponse(
            _iter_file(path, 0, file_size - 1),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
        )

    units, _, range_spec = range_header.partition("=")
    start_s, _, end_s = range_spec.partition("-")
    try:
        start = int(start_s) if start_s else 0
        end = min(int(end_s), file_size - 1) if end_s else file_size - 1
    except ValueError:
        start = end = -1

    if units != "bytes" or start < 0 or start > end:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    return StreamingResponse(
        _iter_file(path, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
        },
    )
