from pathlib import Path

from src.core.errors import NotFoundError

from .base import StorageAdapter

# Files are stored under data/storage/ relative to the project root.
# This folder is in .gitignore — never commit stored files.
_BASE_DIR = Path("data/storage")


class LocalStorageAdapter(StorageAdapter):
    """Stores files on the local filesystem under data/storage/.

    Only for development. Never use in production.
    """

    def __init__(self, base_dir: Path = _BASE_DIR) -> None:
        self._base = base_dir

    def _path(self, key: str) -> Path:
        # Prevent path traversal attacks
        resolved = (self._base / key).resolve()
        if not str(resolved).startswith(str(self._base.resolve())):
            raise ValueError(f"Invalid storage key: {key!r}")
        return resolved

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    async def download(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise NotFoundError(f"Storage key not found: {key}")
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()
