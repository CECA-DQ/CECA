import asyncio
import logging

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)

_MOCK_SCRIPT = """El Gobierno ha presentado hoy un ambicioso plan de infraestructuras ferroviarias
que movilizará dos mil millones de euros en los próximos cuatro años.

El objetivo del plan es modernizar la red de alta velocidad y conectar las principales
ciudades españolas, reduciendo los tiempos de viaje a la mitad.

La financiación procede en un sesenta por ciento de fondos europeos NextGenerationEU,
con el resto a cargo de los presupuestos del Estado, sin impacto en el déficit público.

Según las estimaciones oficiales, la obra generará treinta y cinco mil empleos directos
y hasta ochenta mil indirectos durante la fase de construcción.

Los primeros corredores en iniciar obras serán el mediterráneo y el atlántico,
seleccionados por su densidad de tráfico y su potencial impacto económico."""


class WriteScriptStep(PipelineStep):
    """Generate the voiceover script that links selected segments."""

    name = "write_script"
    description = "Write voiceover script linking the selected segments"

    async def execute(self, state: PipelineState) -> PipelineState:
        script = await self._generate(state)
        result = {"script": script, "word_count": len(script.split())}
        state.voiceover_script = script
        state.step_results[self.name] = result
        return state

    async def _generate(self, state: PipelineState) -> str:
        try:
            from src.adapters.llm.factory import get_llm_provider
            from src.config import settings

            if not settings.groq_api_key and not settings.anthropic_api_key:
                raise ValueError("No LLM API key configured")

            llm = get_llm_provider()
            transcript_text = state.transcript.get("full_text", "")
            segments_text = "\n".join(
                f"- [{s['type']}] {s['reason']}" for s in state.selected_segments
            )
            response = await llm.generate(
                system=(
                    "Eres un redactor experto en informativos de televisión españoles. "
                    "Escribes voz en off clara, directa y con estilo periodístico. "
                    "Usa frases cortas. Máximo 150 palabras."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Escribe la voz en off que une estos segmentos de una rueda de prensa "
                        f"sobre infraestructuras ferroviarias.\n\n"
                        f"Segmentos seleccionados:\n{segments_text}\n\n"
                        f"Transcripción completa:\n{transcript_text}"
                    ),
                }],
                temperature=0.4,
                max_tokens=400,
            )
            return response.text
        except Exception as exc:
            logger.warning("LLM call failed in write_script, using mock: %s", exc)
            await asyncio.sleep(0.5)
            return _MOCK_SCRIPT
