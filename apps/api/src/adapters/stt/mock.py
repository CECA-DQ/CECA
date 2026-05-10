from .base import STTProvider, Transcript, TranscriptSegment

# Fixed transcript that simulates a short press conference.
# Edit to add more variety for demo purposes.
_MOCK_SEGMENTS = [
    TranscriptSegment(start=0.0,  end=4.2,  text="Buenos días a todos, vamos a comenzar la rueda de prensa.", speaker="speaker_0"),
    TranscriptSegment(start=4.5,  end=9.1,  text="El gobierno ha aprobado hoy un paquete de medidas económicas.", speaker="speaker_0"),
    TranscriptSegment(start=9.8,  end=14.3, text="¿Puede detallar cuál será el impacto en los próximos meses?",  speaker="speaker_1"),
    TranscriptSegment(start=15.0, end=21.7, text="El impacto se notará a partir del tercer trimestre del año.", speaker="speaker_0"),
]


class MockSTTProvider(STTProvider):
    """Returns a fixed transcript. No audio processing — for tests and demos."""

    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        with_timestamps: bool = True,
        with_diarization: bool = False,
    ) -> Transcript:
        segments = _MOCK_SEGMENTS
        if not with_diarization:
            segments = [
                TranscriptSegment(start=s.start, end=s.end, text=s.text)
                for s in segments
            ]
        return Transcript(language=language or "es", segments=segments)
