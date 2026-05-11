import asyncio
import logging

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)

_MOCK_PACKAGE = {
    "web_article": (
        "## El Gobierno invertirá 2.000 millones en infraestructuras ferroviarias\n\n"
        "El Ejecutivo ha presentado este martes un plan de modernización de la red ferroviaria "
        "nacional que movilizará **dos mil millones de euros** a lo largo de los próximos cuatro años, "
        "financiados en su mayor parte con fondos europeos del programa NextGenerationEU.\n\n"
        "El plan tiene como objetivo conectar las principales ciudades españolas mediante trenes "
        "de alta velocidad, con el propósito de **reducir los tiempos de viaje a la mitad** respecto "
        "a los actuales. Los primeros corredores en iniciar obras serán el **mediterráneo** y el "
        "**atlántico**, seleccionados por su densidad de tráfico y su impacto económico.\n\n"
        "Según las estimaciones del Ministerio, la ejecución del plan generará **35.000 empleos "
        "directos** durante la fase de construcción y hasta **80.000 indirectos** en la cadena de "
        "suministro industrial.\n\n"
        "Respecto a la financiación, el sesenta por ciento procede de fondos europeos y el resto "
        "del presupuesto nacional. El Gobierno descarta cualquier impacto negativo sobre el déficit "
        "público y asegura que los plazos se cumplirán antes de que finalice la legislatura."
    ),
    "tweet": (
        "🚄 El Gobierno lanza un plan ferroviario de 2.000 M€ para los próximos 4 años. "
        "35.000 empleos directos, corredores mediterráneo y atlántico como prioridad. "
        "Financiado con fondos europeos NextGenerationEU. #Infraestructuras #Tren"
    ),
    "executive_summary": (
        "El Gobierno ha anunciado un plan de inversión de 2.000 millones de euros en infraestructuras "
        "ferroviarias para los próximos cuatro años, financiado principalmente con fondos europeos. "
        "El plan prevé 35.000 empleos directos y prioriza los corredores mediterráneo y atlántico."
    ),
    "angle_proposals": [
        "Impacto medioambiental: ¿cuántas toneladas de CO₂ evitará el trasvase de viajeros de avión a tren?",
        "Comparativa europea: ¿cómo queda España respecto a Francia y Alemania en km de AVE por habitante?",
        "Reacción sindical: las organizaciones de trabajadores valoran la creación de empleo industrial.",
        "Perspectiva regional: qué comunidades autónomas se benefician más del corredor mediterráneo.",
        "Riesgo de ejecución: análisis de los retrasos históricos en las grandes obras ferroviarias españolas.",
    ],
}


class GeneratePackageStep(PipelineStep):
    """Generate the full editorial package: article, tweet, summary, angle proposals."""

    name = "generate_package"
    description = "Generate web article, tweet, executive summary and angle proposals"

    async def execute(self, state: PipelineState) -> PipelineState:
        package = await self._generate(state)
        state.editorial_package = package
        state.step_results[self.name] = package
        return state

    async def _generate(self, state: PipelineState) -> dict:
        try:
            from src.adapters.llm.factory import get_llm_provider
            from src.config import settings

            if not settings.groq_api_key and not settings.anthropic_api_key:
                raise ValueError("No LLM API key configured")

            llm = get_llm_provider()
            transcript_text = state.transcript.get("full_text", "")
            script = state.voiceover_script

            response = await llm.generate(
                system=(
                    "Eres un redactor jefe de un informativo de televisión español. "
                    "Generas paquetes editoriales completos a partir de transcripciones. "
                    "Responde SIEMPRE en JSON válido con las claves: "
                    "web_article, tweet, executive_summary, angle_proposals (lista de strings)."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        "Genera el paquete editorial completo para esta pieza informativa.\n\n"
                        f"Voz en off:\n{script}\n\n"
                        f"Transcripción:\n{transcript_text}\n\n"
                        "- web_article: nota web de 300 palabras en markdown\n"
                        "- tweet: máximo 280 caracteres con hashtags\n"
                        "- executive_summary: 2-3 frases para el editor\n"
                        "- angle_proposals: lista de 5 propuestas de ángulos alternativos"
                    ),
                }],
                temperature=0.5,
                max_tokens=1200,
            )

            import json
            # Strip markdown code fences if present
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text.strip())

        except Exception as exc:
            logger.warning("LLM call failed in generate_package, using mock: %s", exc)
            await asyncio.sleep(1.0)
            return _MOCK_PACKAGE
