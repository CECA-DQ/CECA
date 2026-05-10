from src.config import settings

from .avid import AvidMAMAdapter
from .base import MAMAdapter
from .mock import MockMAMAdapter


def get_mam_adapter() -> MAMAdapter:
    """Return the MAM adapter configured in MAM_PROVIDER env var."""
    match settings.mam_provider:
        case "mock":
            return MockMAMAdapter()
        case "avid":
            return AvidMAMAdapter(base_url="", api_key="")  # wire from settings when implementing
        case _:
            raise ValueError(f"Unknown MAM provider: {settings.mam_provider!r}")
