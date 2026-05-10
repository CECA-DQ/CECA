from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SynthesisResult:
    """Audio output from a TTS provider."""
    audio: bytes        # raw audio bytes (mp3)
    duration_seconds: float
    voice_id: str


class TTSProvider(ABC):
    """Abstract interface for Text-to-Speech providers.

    Implementations: mock.py (tests), elevenlabs.py (production).
    Resolve with get_tts_provider() from factory.py.
    """

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: str,
        language: str = "es",
    ) -> SynthesisResult:
        """Convert text to speech.

        Args:
            text: The voiceover script to synthesize.
            voice_id: Provider-specific voice identifier.
            language: ISO-639-1 code. Used by some providers for pronunciation.
        """
        ...

    @abstractmethod
    def estimate_cost(self, character_count: int) -> float:
        """Estimated cost in USD for synthesizing this many characters."""
        ...
