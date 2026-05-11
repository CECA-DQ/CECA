import asyncio

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep


class IngestStep(PipelineStep):
    """Download raw video to storage and extract technical metadata."""

    name = "ingest"
    description = "Ingest raw video and extract technical metadata"

    async def execute(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(1.5)  # simulate download + probe

        result = {
            "video_key": state.video_key or f"videos/{state.project_id}/raw.mp4",
            "duration_seconds": 187.4,
            "resolution": "1920x1080",
            "codec": "h264",
            "fps": 25.0,
            "audio_codec": "aac",
            "file_size_bytes": 524_288_000,
        }
        state.video_key = result["video_key"]
        state.step_results[self.name] = result
        return state
