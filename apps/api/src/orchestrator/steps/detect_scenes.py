"""Detect scenes step.

Groups transcript segments into logical scenes of roughly 15-20 seconds each,
capped at 20 scenes total. This gives realistic shot counts regardless of
whether the source is a 2-minute clip or a 50-minute interview.

One transcript segment per scene is wrong for long videos — a 50-minute
interview produces 300+ segments which breaks the downstream LLM calls.
"""

import logging

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)

_TARGET_SCENE_DURATION = 15.0  # seconds — aim for scenes of this length
_MAX_SCENES = 20               # hard cap so LLM calls stay within token limits


def _shot_type(duration: float, speaker: str) -> str:
    if duration < 8.0:
        return "close_up"
    if duration > 25.0:
        return "wide_shot"
    if not speaker or speaker == "SPEAKER_00":
        return "medium_shot"
    return "b_roll"


class DetectScenesStep(PipelineStep):
    """Group transcript segments into logical scenes (max 20)."""

    name = "detect_scenes"
    description = "Group transcript into logical scenes for downstream analysis"

    async def execute(self, state: PipelineState) -> PipelineState:
        segments: list[dict] = state.transcript.get("segments", [])

        if not segments:
            result = {"scenes": [], "total_scenes": 0}
            state.scenes = []
            state.step_results[self.name] = result
            return state

        scenes = _group_into_scenes(segments)

        logger.info(
            "Detected %d scenes from %d transcript segments (%.0f–%.0f s)",
            len(scenes), len(segments),
            scenes[0]["start"], scenes[-1]["end"],
        )

        result = {"scenes": scenes, "total_scenes": len(scenes)}
        state.scenes = scenes
        state.step_results[self.name] = result
        return state


def _group_into_scenes(segments: list[dict]) -> list[dict]:
    """Merge segments into at most MAX_SCENES logical scenes."""
    if not segments:
        return []

    total_duration = segments[-1]["end"] - segments[0]["start"]
    # How many scenes do we want? At least 5, at most MAX_SCENES.
    n_scenes = max(5, min(_MAX_SCENES, int(total_duration / _TARGET_SCENE_DURATION)))
    scene_duration = total_duration / n_scenes

    video_start = segments[0]["start"]
    scenes: list[dict] = []

    for i in range(n_scenes):
        scene_start = video_start + i * scene_duration
        scene_end = video_start + (i + 1) * scene_duration

        # Collect segments that fall within this scene window
        window = [
            s for s in segments
            if s["start"] < scene_end and s["end"] > scene_start
        ]

        if not window:
            continue

        # Representative speaker = most frequent in this window
        speakers = [s.get("speaker", "") for s in window if s.get("speaker")]
        speaker = max(set(speakers), key=speakers.count) if speakers else ""

        # Combine the text of all segments in this window
        combined_text = " ".join(s["text"] for s in window)

        duration = scene_end - scene_start
        scenes.append({
            "index": len(scenes),
            "start": round(scene_start, 2),
            "end": round(scene_end, 2),
            "speaker": speaker,
            "type": _shot_type(duration, speaker),
            "text": combined_text,
        })

    return scenes
