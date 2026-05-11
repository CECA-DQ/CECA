import asyncio

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep


class DetectScenesStep(PipelineStep):
    """Detect shot boundaries using scene change analysis."""

    name = "detect_scenes"
    description = "Detect shot boundaries and build scene list"

    async def execute(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(1.0)  # simulate PySceneDetect run

        scenes = [
            {"index": 0,  "start": 0.0,   "end": 12.3,  "type": "wide_shot"},
            {"index": 1,  "start": 12.3,  "end": 24.7,  "type": "medium_shot"},
            {"index": 2,  "start": 24.7,  "end": 35.1,  "type": "close_up"},
            {"index": 3,  "start": 35.1,  "end": 48.9,  "type": "wide_shot"},
            {"index": 4,  "start": 48.9,  "end": 61.2,  "type": "medium_shot"},
            {"index": 5,  "start": 61.2,  "end": 74.8,  "type": "b_roll"},
            {"index": 6,  "start": 74.8,  "end": 89.3,  "type": "medium_shot"},
            {"index": 7,  "start": 89.3,  "end": 103.6, "type": "wide_shot"},
            {"index": 8,  "start": 103.6, "end": 118.2, "type": "b_roll"},
            {"index": 9,  "start": 118.2, "end": 134.0, "type": "medium_shot"},
            {"index": 10, "start": 134.0, "end": 150.5, "type": "close_up"},
            {"index": 11, "start": 150.5, "end": 187.4, "type": "wide_shot"},
        ]

        result = {"scenes": scenes, "total_scenes": len(scenes)}
        state.scenes = scenes
        state.step_results[self.name] = result
        return state
