"""Groq Whisper STT provider.

Uses Groq's whisper-large-v3 model via the Groq API. The response format is
identical to OpenAI's Whisper API, so the same parsing logic applies.
Groq Whisper is free within the standard rate limits and typically faster
than OpenAI's whisper-1.

Chunking for files > 24 MB reuses the same FFmpeg-based logic as WhisperAPIProvider.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from groq import AsyncGroq

from .base import STTProvider, Transcript, TranscriptSegment

logger = logging.getLogger(__name__)

_GROQ_MAX_BYTES = 24 * 1024 * 1024   # 24 MB
_CHUNK_DURATION_SECONDS = 600         # 10-minute chunks
_MODEL = "whisper-large-v3-turbo"     # faster + cheaper; use whisper-large-v3 for max accuracy


class GroqWhisperProvider(STTProvider):
    """Transcription via Groq Whisper API (whisper-large-v3-turbo).

    Drop-in replacement for WhisperAPIProvider. Automatically chunks
    audio files larger than 24 MB into 10-minute segments.
    """

    def __init__(self, api_key: str) -> None:
        self._client = AsyncGroq(api_key=api_key)

    async def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        with_timestamps: bool = True,
        with_diarization: bool = False,
    ) -> Transcript:
        if with_diarization:
            raise NotImplementedError("Diarization not yet supported for GroqWhisperProvider")

        if len(audio) > _GROQ_MAX_BYTES:
            logger.info(
                "Audio is %d MB — splitting into %d-second chunks",
                len(audio) // (1024 * 1024),
                _CHUNK_DURATION_SECONDS,
            )
            return await self._transcribe_chunked(audio, language, with_timestamps)

        return await self._transcribe_single(audio, language, with_timestamps, offset_seconds=0.0)

    async def _transcribe_single(
        self,
        audio: bytes,
        language: str | None,
        with_timestamps: bool,
        offset_seconds: float,
    ) -> Transcript:
        response = await self._client.audio.transcriptions.create(
            model=_MODEL,
            file=("audio.mp3", audio, "audio/mpeg"),
            language=language,
            response_format="verbose_json",
            timestamp_granularities=["segment"] if with_timestamps else [],
        )

        # Groq returns segments as dicts; OpenAI returns objects — handle both.
        def _val(seg, key):
            return seg[key] if isinstance(seg, dict) else getattr(seg, key)

        segments = [
            TranscriptSegment(
                start=_val(seg, "start") + offset_seconds,
                end=_val(seg, "end") + offset_seconds,
                text=_val(seg, "text").strip(),
            )
            for seg in (response.segments or [])
        ]

        lang = response.language if not isinstance(response, dict) else response.get("language")
        return Transcript(language=lang or language or "es", segments=segments)

    async def _transcribe_chunked(
        self,
        audio: bytes,
        language: str | None,
        with_timestamps: bool,
    ) -> Transcript:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            logger.warning("ffmpeg not found — sending oversized audio to Groq Whisper directly")
            return await self._transcribe_single(audio, language, with_timestamps, offset_seconds=0.0)

        chunks = await _split_audio_chunks(audio, ffmpeg, _CHUNK_DURATION_SECONDS)
        logger.info("Split audio into %d chunks for Groq Whisper", len(chunks))

        all_segments: list[TranscriptSegment] = []
        detected_language: str = language or "es"

        for chunk_bytes, offset in chunks:
            result = await self._transcribe_single(chunk_bytes, language, with_timestamps, offset_seconds=offset)
            all_segments.extend(result.segments)
            if result.language:
                detected_language = result.language

        return Transcript(language=detected_language, segments=all_segments)


async def _split_audio_chunks(
    audio: bytes,
    ffmpeg: str,
    chunk_duration: int,
) -> list[tuple[bytes, float]]:
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.mp3"
        input_path.write_bytes(audio)

        segment_pattern = str(Path(tmpdir) / "chunk_%03d.mp3")

        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-i", str(input_path),
            "-f", "segment",
            "-segment_time", str(chunk_duration),
            "-c", "copy",
            segment_pattern,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg segment split failed: {stderr.decode()[-500:]}")

        chunk_files = sorted(Path(tmpdir).glob("chunk_*.mp3"))
        return [
            (chunk_file.read_bytes(), float(i * chunk_duration))
            for i, chunk_file in enumerate(chunk_files)
        ]
