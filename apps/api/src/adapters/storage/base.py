from abc import ABC, abstractmethod


class StorageAdapter(ABC):
    """Abstract interface for file storage backends.

    Keys follow the convention: {tenant_id}/{project_id}/{filename}
    This ensures tenant isolation at the storage level.

    Implementations: local.py (dev), r2.py (production).
    Resolve with get_storage_adapter() from factory.py.
    """

    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload bytes and return the storage key (not a public URL)."""
        ...

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """Download and return the raw bytes for a given key."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete a file. Silent if the key does not exist."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if the key exists in storage."""
        ...
