"""Detect scenes step.

Without downloading the full video, we derive scene boundaries directly from the
transcript: each continuous speech segment (or group of segments by the same
speaker) becomes a scene. This gives real temporal boundaries based on actual
content, not hardcoded values.

When FFmpeg + PySceneDetect are available (future sprint), this step will switch
to pixel-level shot detection. The output format stays identical so no downstream
step needs to change.
"""

import logging

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)

# Shot type heuristics based on segment duration
_SHORT_SCENE_SECS = 8.0   # < 8s → close-up / cut
_LONG_SCENE_SECS = 20.0   # > 20s → wide shot / extended take


def _shot_type_from_segment(seg: dict) -> str:
    duration = seg["end"] - seg["start"]
    speaker = seg.get("speaker", "")
    if duration < _SHORT_SCENE_SECS:
        return "close_up"
    if duration > _LONG_SCENE_SECS:
        return "wide_shot"
    # Segments without a named speaker are often b-roll or presenter narration
    if not speaker or speaker == "SPEAKER_00":
        return "medium_shot"
    return "b_roll"


class DetectScenesStep(PipelineStep):
    """Derive scene boundaries from transcript segments."""

    name = "detect_scenes"
    description = "Derive shot boundaries from transcript timing"

    async def execute(self, state: PipelineState) -> PipelineState:
        segments: list[dict] = state.transcript.get("segments", [])

        if not segments:
            logger.warning("No transcript segments found — returning empty scene list")
            result = {"scenes": [], "total_scenes": 0}
            state.scenes = []
            state.step_results[self.name] = result
            return state

        scenes = [
            {
                "index": i,
                "start": seg["start"],
                "end": seg["end"],
                "speaker": seg.get("speaker", ""),
                "type": _shot_type_from_segment(seg),
            }
            for i, seg in enumerate(segments)
        ]

        logger.info(
            "Detected %d scenes from transcript (duration span: %.1f–%.1f s)",
            len(scenes),
            scenes[0]["start"],
            scenes[-1]["end"],
        )

        result = {"scenes": scenes, "total_scenes": len(scenes)}
        state.scenes = scenes
        state.step_results[self.name] = result
        return state
