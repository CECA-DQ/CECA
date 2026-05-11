import asyncio

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

# Mock visual descriptions per scene type — simulates LLM vision output
_DESCRIPTIONS = {
    "wide_shot":   "Plano general de sala de prensa institucional. Periodistas sentados frente al podio. Buena iluminación.",
    "medium_shot": "Plano medio del portavoz oficial en el podio. Expresión seria. Micro visible. Plano estable.",
    "close_up":    "Primer plano del portavoz. Contacto visual directo con cámara. Alta calidad de imagen.",
    "b_roll":      "Imágenes de recurso de infraestructuras ferroviarias. Tren de alta velocidad en movimiento. Plano dinámico.",
}


class AnalyzeVisualStep(PipelineStep):
    """Analyze a representative frame from each scene using vision LLM."""

    name = "analyze_visual"
    description = "Describe each scene: content, people, shot quality"

    async def execute(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(1.5)  # simulate frame extraction + vision calls

        analysis = [
            {
                "scene_index": scene["index"],
                "description": _DESCRIPTIONS.get(scene["type"], "Plano general sin descripción disponible."),
                "has_people": scene["type"] != "b_roll",
                "shot_type": scene["type"],
                "quality": "stable",
            }
            for scene in state.scenes
        ]

        result = {"scenes_analyzed": len(analysis), "analysis": analysis}
        state.visual_analysis = analysis
        state.step_results[self.name] = result
        return state
