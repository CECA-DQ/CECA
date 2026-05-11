import asyncio

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep


class ComposeVideoStep(PipelineStep):
    """Cut segments, mix voiceover, add music ducking, render lower thirds."""

    name = "compose_video"
    description = "Assemble final MP4 via FFmpeg + Remotion"

    async def execute(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(3.0)  # simulate FFmpeg render time

        video_key = f"output/{state.project_id}/final.mp4"
        result = {
            "video_key": video_key,
            "duration_seconds": 93.5,
            "resolution": "1920x1080",
            "has_voiceover": bool(state.voiceover_audio_key),
            "has_lower_thirds": True,
            "has_music": True,
            "provider": "mock",
        }
        state.composed_video_key = video_key
        state.step_results[self.name] = result
        return state
