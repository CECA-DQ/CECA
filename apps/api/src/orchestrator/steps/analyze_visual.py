"""Analyze visual step.

Without the downloaded video, we use the LLM to infer the probable visual context
of each scene from its transcript text. This gives real, content-aware descriptions
instead of hardcoded strings.

When Claude Vision + frame extraction are implemented (future sprint), this step
will send actual video frames. The output format is identical.
"""

import json
import logging

from src.adapters.llm.factory import get_llm_provider
from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep
from src.services.prompt_store import get_prompt_store

logger = logging.getLogger(__name__)

_MOCK_ANALYSIS = [
    {
        "scene_index": 0,
        "description": "Plano general de sala de prensa institucional. Periodistas sentados frente al podio.",
        "has_people": True,
        "shot_type": "wide_shot",
        "quality": "stable",
    }
]


def _mock_from_scenes(scenes: list[dict]) -> list[dict]:
    type_map = {
        "wide_shot": ("Plano general de la escena principal. Contexto amplio visible.", True),
        "medium_shot": ("Plano medio del interlocutor principal. Expresión visible.", True),
        "close_up": ("Primer plano del hablante. Alto detalle facial.", True),
        "b_roll": ("Imágenes de recurso relacionadas con el tema tratado.", False),
    }
    return [
        {
            "scene_index": s["index"],
            "description": type_map.get(s["type"], ("Plano general.", True))[0],
            "has_people": type_map.get(s["type"], ("", True))[1],
            "shot_type": s["type"],
            "quality": "stable",
        }
        for s in scenes
    ]


class AnalyzeVisualStep(PipelineStep):
    """Infer visual context for each scene using LLM analysis of transcript text."""

    name = "analyze_visual"
    description = "Infer visual context from transcript via LLM"

    async def execute(self, state: PipelineState) -> PipelineState:
        scenes = state.scenes
        transcript_segments = state.transcript.get("segments", [])

        if not scenes or not transcript_segments:
            logger.warning("No scenes or transcript — skipping visual analysis")
            result = {"scenes_analyzed": 0, "analysis": []}
            state.visual_analysis = []
            state.step_results[self.name] = result
            return state

        # Enrich scenes with their transcript text for the prompt
        seg_by_index = {i: s for i, s in enumerate(transcript_segments)}
        scenes_with_text = [
            {
                "scene_index": s["index"],
                "start": s["start"],
                "end": s["end"],
                "shot_type": s["type"],
                "text": seg_by_index.get(s["index"], {}).get("text", ""),
            }
            for s in scenes
        ]

        try:
            analysis = await self._call_llm(scenes_with_text)
            logger.info("Visual analysis completed: %d scenes", len(analysis))
        except Exception as exc:
            logger.warning(
                "Visual analysis LLM call failed (%s: %s), using mock",
                type(exc).__name__, exc,
            )
            analysis = _mock_from_scenes(scenes)

        result = {"scenes_analyzed": len(analysis), "analysis": analysis}
        state.visual_analysis = analysis
        state.step_results[self.name] = result
        return state

    async def _call_llm(self, scenes_with_text: list[dict]) -> list[dict]:
        llm = get_llm_provider()
        store = get_prompt_store()
        prompt = store.render(
            "pipeline/analyze_visual",
            scenes=scenes_with_text,
            total_scenes=len(scenes_with_text),
        )

        response = await llm.generate(
            system=prompt.system,
            messages=[{"role": "user", "content": prompt.user}],
            temperature=0.3,
            max_tokens=2000,
        )

        raw = response.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array, got {type(data)}")

        return data
