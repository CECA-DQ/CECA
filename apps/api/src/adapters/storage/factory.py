from src.config import settings

from .base import StorageAdapter
from .local import LocalStorageAdapter
from .r2 import R2StorageAdapter


def get_storage_adapter() -> StorageAdapter:
    """Return the storage adapter configured in STORAGE_PROVIDER env var."""
    match settings.storage_provider:
        case "local":
            return LocalStorageAdapter()
        case "r2":
            return R2StorageAdapter(
                account_id=settings.r2_account_id,
                access_key_id=settings.r2_access_key_id,
                secret_access_key=settings.r2_secret_access_key,
                bucket=settings.r2_bucket,
            )
        case _:
            raise ValueError(f"Unknown storage provider: {settings.storage_provider!r}")
