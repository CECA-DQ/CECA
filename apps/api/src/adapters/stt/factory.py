from src.config import settings

from .base import STTProvider
from .mock import MockSTTProvider
from .whisper import WhisperAPIProvider


def get_stt_provider() -> STTProvider:
    """Return the STT provider configured in STT_PROVIDER env var."""
    match settings.stt_provider:
        case "mock":
            return MockSTTProvider()
        case "whisper_api":
            return WhisperAPIProvider(api_key=settings.openai_api_key)
        case _:
            raise ValueError(f"Unknown STT provider: {settings.stt_provider!r}")
