"""Storage-path containment: UUID dirs stay under the configured root, and
upload filenames only contribute a safe suffix on original.{ext}."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.datastructures import UploadFile

from app.core.storage import (
    _contained_under,
    _item_dir,
    _safe_upload_suffix,
    _save_upload,
    delete_meeting_files,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("notes.md", ".md"),
        ("doc.markdown", ".markdown"),
        ("audio.WAV", ".wav"),
        ("no-extension", ".bin"),
        ("", ".bin"),
        (None, ".bin"),
        ("../../etc/passwd", ".bin"),
        ("foo.wav/../../etc/passwd", ".bin"),
        ("original.exe", ".exe"),  # suffix shape only; API allowlists the type
        ("file.name.with.dots.txt", ".txt"),
        ("weird..", ".bin"),
        ("x.thisiswaytoolongext", ".bin"),
    ],
)
def test_safe_upload_suffix(filename, expected):
    assert _safe_upload_suffix(filename) == expected


def test_item_dir_stays_under_root(tmp_path: Path):
    item_id = uuid4()
    dest = _item_dir(str(tmp_path), item_id)
    assert dest == (tmp_path / str(item_id)).resolve()
    assert dest.is_relative_to(tmp_path.resolve())


def test_contained_under_rejects_escape(tmp_path: Path):
    root = tmp_path / "audio"
    root.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        _contained_under(root, tmp_path / "outside")


@pytest.mark.asyncio
async def test_save_upload_keeps_file_inside_dest_dir(tmp_path: Path):
    dest_dir = tmp_path / str(uuid4())
    upload = UploadFile(
        filename="../../etc/passwd.md",
        file=BytesIO(b"hello kb"),
    )
    written = await _save_upload(dest_dir, upload, max_mb=1)
    path = Path(written)
    assert path.is_relative_to(dest_dir.resolve())
    assert path.name == "original.md"
    assert path.read_text() == "hello kb"
    # Traversal in the filename must not create anything outside dest_dir.
    assert not (tmp_path / "etc").exists()
    assert not (tmp_path / "passwd.md").exists()


@pytest.mark.asyncio
async def test_save_upload_fallback_suffix_stays_inside(tmp_path: Path):
    dest_dir = tmp_path / str(uuid4())
    upload = UploadFile(filename="../../etc/passwd", file=BytesIO(b"x"))
    written = await _save_upload(dest_dir, upload, max_mb=1)
    path = Path(written)
    assert path.is_relative_to(dest_dir.resolve())
    assert path.name == "original.bin"


def test_delete_meeting_files_swallows_missing(tmp_path: Path, monkeypatch):
    from app.core import storage as storage_mod

    monkeypatch.setattr(
        storage_mod, "get_settings", lambda: type("S", (), {"audio_storage_path": str(tmp_path)})()
    )
    delete_meeting_files(uuid4())  # nothing there — must not raise
