from .base import MAMAdapter, MediaAsset


class AvidMAMAdapter(MAMAdapter):
    """AVID MediaCentral adapter.

    To be implemented once the first client is signed.
    API reference: AVID MediaCentral Platform Services REST API.

    Required settings (add to config.py when implementing):
        AVID_BASE_URL — e.g. https://mediacentral.client.com/api
        AVID_API_KEY  — service account key
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._api_key = api_key

    async def search(self, query: str, tenant_id: str, limit: int = 20) -> list[MediaAsset]:
        raise NotImplementedError("AvidMAMAdapter.search not yet implemented")

    async def get_asset(self, asset_id: str, tenant_id: str) -> MediaAsset:
        raise NotImplementedError("AvidMAMAdapter.get_asset not yet implemented")

    async def download(self, asset_id: str, tenant_id: str) -> bytes:
        raise NotImplementedError("AvidMAMAdapter.download not yet implemented")
