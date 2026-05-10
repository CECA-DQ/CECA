from src.core.errors import NotFoundError

from .base import MAMAdapter, MediaAsset

# Fixed catalogue for demos and tests. Edit freely — nothing persists.
_MOCK_ASSETS: list[MediaAsset] = [
    MediaAsset(
        id="mock-001",
        title="Rueda de prensa — presidencia",
        duration_seconds=312.0,
        storage_url="data/videos/mock-001.mp4",
        metadata={"location": "Madrid", "date": "2026-05-10"},
    ),
    MediaAsset(
        id="mock-002",
        title="Acto oficial — inauguración",
        duration_seconds=540.0,
        storage_url="data/videos/mock-002.mp4",
        metadata={"location": "Barcelona", "date": "2026-05-09"},
    ),
    MediaAsset(
        id="mock-003",
        title="Entrevista — portavoz ministerio",
        duration_seconds=187.0,
        storage_url="data/videos/mock-003.mp4",
        metadata={"location": "Madrid", "date": "2026-05-08"},
    ),
]


class MockMAMAdapter(MAMAdapter):
    """In-memory MAM for demos and unit tests. No external dependencies."""

    async def search(self, query: str, tenant_id: str, limit: int = 20) -> list[MediaAsset]:
        q = query.lower()
        results = [a for a in _MOCK_ASSETS if q in a.title.lower()]
        return results[:limit]

    async def get_asset(self, asset_id: str, tenant_id: str) -> MediaAsset:
        for asset in _MOCK_ASSETS:
            if asset.id == asset_id:
                return asset
        raise NotFoundError(f"Asset not found: {asset_id}")

    async def download(self, asset_id: str, tenant_id: str) -> bytes:
        raise NotImplementedError("MockMAMAdapter does not serve bytes — use a real storage adapter.")
