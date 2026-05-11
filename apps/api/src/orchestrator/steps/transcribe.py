"""Transcribe step: audio → text with per-segment timestamps.

Uses the STT adapter configured via STT_PROVIDER env var (default: whisper_api).
Falls back to mock transcript on any error so the pipeline can continue during demos.

Audio extraction strategy (from state.video_key):
- YouTube URL → yt-dlp downloads best audio as mp3
- Direct video URL → httpx download + ffmpeg audio extract
- Local storage key → ffmpeg audio extract from disk
"""

import logging

from src.adapters.stt.factory import get_stt_provider
from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)

_MOCK_SEGMENTS = [
    {"start": 0.0,   "end": 5.2,  "speaker": "SPEAKER_00", "text": "Buenos días a todos. Vamos a comenzar la rueda de prensa sobre el nuevo plan de infraestructuras."},
    {"start": 5.4,   "end": 14.1, "speaker": "SPEAKER_00", "text": "Este plan supone una inversión de dos mil millones de euros en los próximos cuatro años para modernizar la red ferroviaria nacional."},
    {"start": 14.5,  "end": 22.8, "speaker": "SPEAKER_00", "text": "El objetivo es conectar las principales ciudades con trenes de alta velocidad y reducir los tiempos de viaje a la mitad."},
    {"start": 23.1,  "end": 31.4, "speaker": "SPEAKER_01", "text": "¿Puede explicar cómo se va a financiar este proyecto y qué impacto tendrá en el déficit público?"},
    {"start": 32.0,  "end": 45.7, "speaker": "SPEAKER_00", "text": "La financiación proviene de fondos europeos NextGenerationEU, que cubren el sesenta por ciento, y del presupuesto nacional el resto. No se prevé un aumento del déficit."},
    {"start": 46.2,  "end": 58.3, "speaker": "SPEAKER_01", "text": "¿Cuántos empleos directos e indirectos generará la obra durante la fase de construcción?"},
    {"start": 59.0,  "end": 72.1, "speaker": "SPEAKER_00", "text": "Las estimaciones apuntan a unos treinta y cinco mil empleos directos durante los cuatro años de construcción y unos ochenta mil empleos indirectos en la cadena de suministro."},
    {"start": 73.0,  "end": 84.5, "speaker": "SPEAKER_02", "text": "¿Qué criterios se han utilizado para seleccionar los tramos prioritarios del proyecto?"},
    {"start": 85.1,  "end": 102.4, "speaker": "SPEAKER_00", "text": "Se han priorizado los corredores con mayor densidad de tráfico y mayor impacto económico. El corredor mediterráneo y el corredor atlántico son los primeros en iniciar obras."},
]


def _count_speakers(segments: list[dict]) -> int:
    return len({s.get("speaker") for s in segments if s.get("speaker")})


class TranscribeStep(PipelineStep):
    """Transcribe audio with per-segment timestamps using Whisper API."""

    name = "transcribe"
    description = "Audio → text with timestamps via Whisper API"

    async def execute(self, state: PipelineState) -> PipelineState:
        video_key = state.video_key

        try:
            audio_bytes = await self._get_audio(video_key)
            transcript = await self._transcribe(audio_bytes)
            segments = [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "speaker": seg.speaker or "SPEAKER_00",
                    "text": seg.text,
                }
                for seg in transcript.segments
            ]
            language = transcript.language
            logger.info(
                "Transcription completed: %d segments, language=%s, video_key=%s",
                len(segments), language, video_key,
            )
        except Exception as exc:
            logger.warning(
                "Transcription failed (%s), falling back to mock: %s",
                type(exc).__name__, exc,
            )
            segments = _MOCK_SEGMENTS
            language = "es"

        full_text = " ".join(s["text"] for s in segments)
        result = {
            "language": language,
            "segments": segments,
            "full_text": full_text,
            "total_segments": len(segments),
            "speakers_detected": _count_speakers(segments),
        }
        state.transcript = result
        state.step_results[self.name] = result
        return state

    async def _get_audio(self, video_key: str) -> bytes:
        """Extract audio from the video source."""
        from src.adapters.stt.audio import extract_audio_from_url, extract_audio_from_file

        if video_key.startswith(("http://", "https://")):
            logger.info("Extracting audio from URL: %s", video_key)
            return await extract_audio_from_url(video_key)

        # Local storage key — assume it's a path on disk
        logger.info("Extracting audio from local file: %s", video_key)
        return await extract_audio_from_file(video_key)

    async def _transcribe(self, audio_bytes: bytes):
        """Transcribe audio bytes using the configured STT provider."""
        stt = get_stt_provider()
        return await stt.transcribe(
            audio=audio_bytes,
            language=None,        # auto-detect language
            with_timestamps=True,
            with_diarization=False,
        )
