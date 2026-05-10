"""Unit tests for the storage adapter.

Run against LocalStorageAdapter pointing to a temp directory — no cloud needed.
"""
import pytest
from pathlib import Path

from src.adapters.storage.local import LocalStorageAdapter
from src.core.errors import NotFoundError


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(base_dir=tmp_path)


async def test_upload_and_download_roundtrip(storage: LocalStorageAdapter) -> None:
    await storage.upload("tenant-1/project-1/clip.mp4", b"fake-video-bytes")
    result = await storage.download("tenant-1/project-1/clip.mp4")
    assert result == b"fake-video-bytes"


async def test_exists_returns_true_after_upload(storage: LocalStorageAdapter) -> None:
    await storage.upload("tenant-1/audio.mp3", b"audio")
    assert await storage.exists("tenant-1/audio.mp3") is True


async def test_exists_returns_false_for_missing_key(storage: LocalStorageAdapter) -> None:
    assert await storage.exists("tenant-1/missing.mp4") is False


async def test_download_missing_key_raises_not_found(storage: LocalStorageAdapter) -> None:
    with pytest.raises(NotFoundError):
        await storage.download("tenant-1/missing.mp4")


async def test_delete_removes_file(storage: LocalStorageAdapter) -> None:
    await storage.upload("tenant-1/to-delete.mp4", b"data")
    await storage.delete("tenant-1/to-delete.mp4")
    assert await storage.exists("tenant-1/to-delete.mp4") is False


async def test_delete_is_silent_for_missing_key(storage: LocalStorageAdapter) -> None:
    await storage.delete("tenant-1/never-existed.mp4")  # must not raise
