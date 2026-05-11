"""Generate TV pieces step.

From the transcript and voiceover script, generates the five broadcast
production pieces needed to air the story:
  1. presenter_lead  — Cola de presentador
  2. soundbites      — Totales extraídos
  3. vtr_script      — Guión VTR
  4. graphics        — Grafismo / Pantallón
  5. rundown         — Escaleta sugerida
"""

import json
import logging

from src.adapters.llm.factory import get_llm_provider
from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep
from src.services.prompt_store import get_prompt_store

logger = logging.getLogger(__name__)

_REQUIRED_KEYS = {"presenter_lead", "soundbites", "vtr_script", "graphics", "rundown"}

_MOCK_TV_PIECES = {
    "presenter_lead": {
        "slug": "INFRAESTRUCTURAS FERROVIARIAS",
        "text": (
            "El Gobierno anuncia una inversión histórica en la red ferroviaria nacional. "
            "Dos mil millones de euros en cuatro años para conectar las principales ciudades "
            "con trenes de alta velocidad. El sesenta por ciento llega de fondos europeos."
        ),
        "background_shots": [
            "Plano tren AVE en movimiento",
            "Imagen archivo Ministerio de Transportes",
            "Mapa red ferroviaria nacional",
            "Plano obras infraestructura",
        ],
        "estimated_duration_seconds": 25,
    },
    "soundbites": [
        {
            "slug": "TOT PORTAVOZ INVERSIÓN",
            "speaker_name": "Portavoz del Gobierno",
            "speaker_role": "Portavoz oficial",
            "quote": "Esta inversión es la mayor en infraestructuras ferroviarias de la última década y conectará a millones de ciudadanos.",
            "timecode": "00:45",
            "duration_seconds": 8,
        },
        {
            "slug": "TOT PORTAVOZ EMPLEO",
            "speaker_name": "Portavoz del Gobierno",
            "speaker_role": "Portavoz oficial",
            "quote": "Generaremos treinta y cinco mil empleos directos durante la construcción y hasta ochenta mil empleos indirectos en la cadena de suministro.",
            "timecode": "02:10",
            "duration_seconds": 10,
        },
    ],
    "vtr_script": {
        "presenter_intro": (
            "El Ejecutivo presenta hoy su plan más ambicioso en décadas. "
            "Dos mil millones de euros para modernizar la red ferroviaria. "
            "Les contamos los detalles."
        ),
        "narration": {
            "apertura": (
                "Una inversión que promete cambiar la forma en que los españoles se desplazan. "
                "El Gobierno ha presentado este martes el mayor plan ferroviario en años."
            ),
            "desarrollo": (
                "Dos mil millones de euros a lo largo de cuatro años. El objetivo es claro: "
                "conectar las principales ciudades con alta velocidad y reducir los tiempos de viaje a la mitad.\n\n"
                "((ENTRA TOTAL 1))\n\n"
                "La financiación es una pieza clave del proyecto. El sesenta por ciento procede de fondos "
                "europeos NextGenerationEU, el resto del presupuesto nacional. El Gobierno descarta "
                "cualquier impacto sobre el déficit público.\n\n"
                "((ENTRA TOTAL 2))\n\n"
                "Los primeros corredores en arrancar serán el mediterráneo y el atlántico, "
                "seleccionados por densidad de tráfico e impacto económico."
            ),
            "cierre": (
                "Queda por ver si los plazos se cumplen. Los antecedentes en grandes obras "
                "ferroviarias invitan a la cautela."
            ),
        },
        "estimated_duration": "1:50",
    },
    "graphics": {
        "headline": "PLAN FERROVIARIO: 2.000 MILLONES EN 4 AÑOS",
        "bullets": [
            "60% financiado con fondos europeos NextGenerationEU",
            "35.000 empleos directos en fase de construcción",
            "Prioridad: corredor mediterráneo y atlántico",
            "Objetivo: reducir tiempos de viaje a la mitad",
        ],
        "source": "Ministerio de Transportes — Rueda de prensa",
        "graphic_type": "PANTALLÓN COMPLETO",
    },
    "rundown": {
        "items": [
            {"order": 1, "element": "Cola presentador", "duration_seconds": 25, "notes": "Dar paso directo al VTR"},
            {"order": 2, "element": "Pantallón datos", "duration_seconds": 8, "notes": "Mostrar durante la cola"},
            {"order": 3, "element": "VTR narración + totales", "duration_seconds": 110, "notes": "Incluye 2 totales intercalados"},
            {"order": 4, "element": "TOT PORTAVOZ INVERSIÓN", "duration_seconds": 8, "notes": "Primer total del VTR"},
            {"order": 5, "element": "TOT PORTAVOZ EMPLEO", "duration_seconds": 10, "notes": "Segundo total del VTR"},
        ],
        "total_duration_seconds": 161,
        "total_duration_formatted": "2:41",
    },
}


def _parse(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("Response is not a JSON object")
    missing = _REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"Missing keys: {sorted(missing)}")
    return data


class GenerateTVPiecesStep(PipelineStep):
    """Generate the five TV broadcast production pieces from the transcript."""

    name = "generate_tv_pieces"
    description = "Generate presenter lead, soundbites, VTR script, graphics, and rundown"

    async def execute(self, state: PipelineState) -> PipelineState:
        transcript_text = state.transcript.get("full_text", "")
        if not transcript_text:
            segments = state.transcript.get("segments", [])
            transcript_text = " ".join(s.get("text", "") for s in segments)

        if not transcript_text:
            logger.warning("No transcript available — using mock TV pieces")
            state.tv_pieces = _MOCK_TV_PIECES
            state.step_results[self.name] = _MOCK_TV_PIECES
            return state

        try:
            llm = get_llm_provider()
            prompt = get_prompt_store().render(
                "pipeline/generate_tv_pieces",
                transcript=transcript_text,
                voiceover_script=state.voiceover_script or "",
            )
            response = await llm.generate(
                system=prompt.system,
                messages=[{"role": "user", "content": prompt.user}],
                temperature=0.4,
                max_tokens=3000,
            )
            tv_pieces = _parse(response.text)
            logger.info("TV pieces generated successfully")
        except Exception as exc:
            logger.warning("generate_tv_pieces failed (%s: %s) — using mock", type(exc).__name__, exc)
            tv_pieces = _MOCK_TV_PIECES

        state.tv_pieces = tv_pieces
        state.step_results[self.name] = tv_pieces
        return state
