from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TranscriptSegment:
    """A single timed chunk of speech."""
    start: float        # seconds from video start
    end: float          # seconds from video start
    text: str
    speaker: str | None = None  # populated when diarization is enabled


@dataclass
class Transcript:
    """Full transcription result returned by any STT provider."""
    language: str
    segments: list[TranscriptSegment]
    full_text: str = field(init=False)

    def __post_init__(self) -> None:
        self.full_text = " ".join(s.text.strip() for s in self.segments)


class STTProvider(ABC):
    """Abstract interface for Speech-to-Text providers.

    Implementations: mock.py (tests), whisper.py (production).
    Resolve with get_stt_provider() from factory.py.
    """

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        with_timestamps: bool = True,
        with_diarization: bool = False,
    ) -> Transcript:
        """Transcribe raw audio bytes.

        Args:
            audio: Raw audio bytes (mp3, wav, m4a supported by Whisper).
            language: ISO-639-1 code (e.g. 'es'). None = auto-detect.
            with_timestamps: Include start/end times per segment.
            with_diarization: Attempt to label speakers (speaker_0, speaker_1…).
        """
        ...
