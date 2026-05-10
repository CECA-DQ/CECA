from src.config import settings

from .base import TTSProvider
from .elevenlabs import ElevenLabsProvider
from .mock import MockTTSProvider


def get_tts_provider() -> TTSProvider:
    """Return the TTS provider configured in TTS_PROVIDER env var."""
    match settings.tts_provider:
        case "mock":
            return MockTTSProvider()
        case "elevenlabs":
            return ElevenLabsProvider(api_key=settings.elevenlabs_api_key)
        case _:
            raise ValueError(f"Unknown TTS provider: {settings.tts_provider!r}")
