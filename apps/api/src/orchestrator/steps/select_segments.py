"""Select segments step.

Uses the LLM to choose which transcript segments to include in the final piece,
applying editorial rules: intro → soundbites → question/answer → closing.

Falls back to selecting the first 6 segments if the LLM call fails.
"""

import json
import logging

from src.adapters.llm.factory import get_llm_provider
from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep
from src.services.prompt_store import get_prompt_store

logger = logging.getLogger(__name__)


def _mock_from_segments(segments: list[dict]) -> list[dict]:
    """Select first few segments as a safe fallback."""
    types = ["intro", "soundbite", "soundbite", "question_answer", "soundbite", "closing"]
    selected = segments[:min(6, len(segments))]
    return [
        {
            "scene_index": i,
            "start": seg["start"],
            "end": seg["end"],
            "type": types[i] if i < len(types) else "soundbite",
            "reason": "Selección automática de fallback.",
        }
        for i, seg in enumerate(selected)
    ]


class SelectSegmentsStep(PipelineStep):
    """Select the best segments for the edited piece using the LLM."""

    name = "select_segments"
    description = "Select and order key segments via LLM editorial judgment"

    async def execute(self, state: PipelineState) -> PipelineState:
        segments: list[dict] = state.transcript.get("segments", [])

        if not segments:
            logger.warning("No transcript segments — skipping segment selection")
            result = {"total_selected": 0, "estimated_duration_seconds": 0, "selected_segments": []}
            state.selected_segments = []
            state.step_results[self.name] = result
            return state

        try:
            selected = await self._call_llm(segments)
            logger.info("Segment selection: %d segments chosen", len(selected))
        except Exception as exc:
            logger.warning(
                "Segment selection LLM call failed (%s: %s), using fallback",
                type(exc).__name__, exc,
            )
            selected = _mock_from_segments(segments)

        duration = sum(s["end"] - s["start"] for s in selected)
        result = {
            "total_selected": len(selected),
            "estimated_duration_seconds": round(duration, 1),
            "selected_segments": selected,
        }
        state.selected_segments = selected
        state.step_results[self.name] = result
        return state

    async def _call_llm(self, segments: list[dict]) -> list[dict]:
        llm = get_llm_provider()
        store = get_prompt_store()
        prompt = store.render(
            "pipeline/select_segments",
            segments=segments,
            total_segments=len(segments),
        )

        response = await llm.generate(
            system=prompt.system,
            messages=[{"role": "user", "content": prompt.user}],
            temperature=0.4,
            max_tokens=1500,
        )

        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array, got {type(data)}")
        if not data:
            raise ValueError("LLM returned empty selection")

        return data
