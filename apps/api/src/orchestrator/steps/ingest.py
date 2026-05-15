"""Ingest step: validate source and extract technical metadata.

For URLs: uses yt-dlp (YouTube) or ffprobe (direct video) to get metadata
without downloading the full file. Falls back to safe mock values on error.

The step sets state.video_key and records technical metadata in step_results.
"""

import logging

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)

_MOCK_METADATA = {
    "duration_seconds": 187.4,
    "resolution": "1920x1080",
    "codec": "h264",
    "fps": 25.0,
    "audio_codec": "aac",
    "file_size_bytes": 524_288_000,
    "title": "",
}


class IngestStep(PipelineStep):
    """Ingest raw video and extract technical metadata."""

    name = "ingest"
    description = "Ingest raw video and extract technical metadata"

    async def execute(self, state: PipelineState) -> PipelineState:
        video_key = state.video_key or f"videos/{state.project_id}/raw.mp4"
        metadata = await self._probe(video_key)

        result = {
            "video_key": video_key,
            **metadata,
        }
        state.video_key = video_key
        state.step_results[self.name] = result
        return state

    async def _probe(self, video_key: str) -> dict:
        if video_key.startswith(("http://", "https://")):
            try:
                from src.adapters.stt.audio import get_video_metadata_from_url
                meta = await get_video_metadata_from_url(video_key)
                logger.info(
                    "Probed video metadata: duration=%.1fs resolution=%s",
                    meta.get("duration_seconds", 0),
                    meta.get("resolution", "unknown"),
                )
                return meta
            except Exception as exc:
                logger.warning(
                    "Video probe failed (%s: %s), using mock metadata",
                    type(exc).__name__, exc,
                )
        else:
            try:
                from pathlib import Path
                from src.adapters.stt.audio import get_video_metadata_from_local_file
                local_path = str((Path("data/storage") / video_key).resolve())
                meta = await get_video_metadata_from_local_file(local_path)
                logger.info(
                    "Probed local file metadata: duration=%.1fs resolution=%s",
                    meta.get("duration_seconds", 0),
                    meta.get("resolution", "unknown"),
                )
                return meta
            except Exception as exc:
                logger.warning(
                    "Local file probe failed (%s: %s), using mock metadata",
                    type(exc).__name__, exc,
                )

        return dict(_MOCK_METADATA)
