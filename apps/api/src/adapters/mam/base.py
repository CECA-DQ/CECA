from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MediaAsset:
    """Represents a video asset returned by any MAM system."""
    id: str
    title: str
    duration_seconds: float
    storage_url: str
    metadata: dict = field(default_factory=dict)


class MAMAdapter(ABC):
    """Abstract interface for Media Asset Manager systems.

    Any MAM (mock, AVID, Dalet…) must implement these three methods.
    Business logic only calls this interface — never a concrete class directly.

    Implementations: mock.py (demo/tests), avid.py (production, pending).
    Resolve with get_mam_adapter() from factory.py.
    """

    @abstractmethod
    async def search(self, query: str, tenant_id: str, limit: int = 20) -> list[MediaAsset]:
        """Full-text search over available assets for a given tenant."""
        ...

    @abstractmethod
    async def get_asset(self, asset_id: str, tenant_id: str) -> MediaAsset:
        """Fetch a single asset by ID. Raises NotFoundError if missing."""
        ...

    @abstractmethod
    async def download(self, asset_id: str, tenant_id: str) -> bytes:
        """Download the raw video bytes for processing."""
        ...
