from openai import AsyncOpenAI

from .base import STTProvider, Transcript, TranscriptSegment


class WhisperAPIProvider(STTProvider):
    """Transcription via OpenAI Whisper API (whisper-1).

    Sends audio bytes to the API and maps the verbose_json response
    to our internal Transcript format.
    """

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)

    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        with_timestamps: bool = True,
        with_diarization: bool = False,
    ) -> Transcript:
        response = await self._client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.mp3", audio, "audio/mpeg"),
            language=language,
            response_format="verbose_json",
            timestamp_granularities=["segment"] if with_timestamps else [],
        )

        segments = [
            TranscriptSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
            )
            for seg in (response.segments or [])
        ]

        # Diarization is not supported by Whisper API natively.
        # Wire a separate diarization service here when needed (e.g. pyannote).
        if with_diarization:
            raise NotImplementedError("Diarization not yet supported for WhisperAPIProvider")

        return Transcript(language=response.language, segments=segments)
