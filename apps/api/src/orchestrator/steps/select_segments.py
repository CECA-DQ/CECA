import asyncio

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep


class SelectSegmentsStep(PipelineStep):
    """Select which segments to include in the final piece and in what order."""

    name = "select_segments"
    description = "Select and order segments for the final edited piece"

    async def execute(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(0.8)

        # Editorial selection: intro wide shot → key soundbites → b-roll → closing
        selected = [
            {
                "scene_index": 0,
                "start": 0.0,
                "end": 12.3,
                "type": "intro",
                "reason": "Plano general de apertura que establece el contexto de la rueda de prensa.",
            },
            {
                "scene_index": 2,
                "start": 24.7,
                "end": 35.1,
                "type": "soundbite",
                "reason": "Declaración principal sobre la inversión de 2.000 millones.",
            },
            {
                "scene_index": 5,
                "start": 61.2,
                "end": 74.8,
                "type": "b_roll",
                "reason": "Imágenes de recurso ferroviario para ilustrar el plan.",
            },
            {
                "scene_index": 6,
                "start": 74.8,
                "end": 89.3,
                "type": "soundbite",
                "reason": "Respuesta sobre el impacto en el empleo — dato clave para la audiencia.",
            },
            {
                "scene_index": 8,
                "start": 103.6,
                "end": 118.2,
                "type": "b_roll",
                "reason": "Segunda imagen de recurso para separar los dos grandes bloques informativos.",
            },
            {
                "scene_index": 10,
                "start": 134.0,
                "end": 150.5,
                "type": "soundbite",
                "reason": "Cierre con los corredores prioritarios. Información concreta y memorable.",
            },
        ]

        result = {
            "total_selected": len(selected),
            "estimated_duration_seconds": sum(s["end"] - s["start"] for s in selected),
            "selected_segments": selected,
        }
        state.selected_segments = selected
        state.step_results[self.name] = result
        return state
