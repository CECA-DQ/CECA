import asyncio
import json
import logging

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)

_REQUIRED_KEYS = {"web_article", "tweet", "executive_summary", "angle_proposals"}

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


class _GuardrailError(Exception):
    """Raised when the LLM output does not meet the guardrail constraints."""


def _parse_and_validate(text: str) -> dict:
    """Parse JSON response and validate required keys. Raises _GuardrailError on failure."""
    cleaned = text.strip()

    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise _GuardrailError(f"Response is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise _GuardrailError("Response is not a JSON object")

    missing = _REQUIRED_KEYS - data.keys()
    if missing:
        raise _GuardrailError(f"Missing required keys: {sorted(missing)}")

    if not isinstance(data.get("angle_proposals"), list):
        raise _GuardrailError("'angle_proposals' must be a list")

    if len(data.get("tweet", "")) > 280:
        raise _GuardrailError(
            f"Tweet exceeds 280 characters ({len(data['tweet'])} chars)"
        )

    return data


class GeneratePackageStep(PipelineStep):
    """Generate the full editorial package: article, tweet, summary, angle proposals.

    Guardrails:
    - Output must be valid JSON with all 4 required keys.
    - tweet must not exceed 280 characters.
    - angle_proposals must be a list.
    - On validation failure: one retry with a correction instruction.
    - If LLM is unavailable or fails twice: falls back to mock package.
    """

    name = "generate_package"
    description = "Generate web article, tweet, executive summary and angle proposals"

    async def execute(self, state: PipelineState) -> PipelineState:
        package = await self._generate(state)
        package["source"] = "llm" if package is not _MOCK_PACKAGE else "mock"
        state.editorial_package = package
        state.step_results[self.name] = package
        return state

    async def _generate(self, state: PipelineState) -> dict:
        try:
            from src.adapters.llm.factory import get_llm_provider
            from src.services.prompt_store import get_prompt_store

            llm = get_llm_provider()
            prompt = get_prompt_store().render(
                "pipeline/generate_package",
                transcript=state.transcript.get("full_text", ""),
                script=state.voiceover_script,
            )
            messages = [{"role": "user", "content": prompt.user}]

            # Attempt 1
            response = await llm.generate(
                system=prompt.system,
                messages=messages,
                temperature=0.5,
                max_tokens=1200,
            )
            try:
                return _parse_and_validate(response.text)
            except _GuardrailError as guard_err:
                logger.warning(
                    "generate_package guardrail failed (attempt 1): %s — retrying", guard_err
                )

            # Attempt 2 — show the bad output and ask for a correction
            messages = messages + [
                {"role": "assistant", "content": response.text},
                {
                    "role": "user",
                    "content": (
                        f"El JSON anterior no es válido: {guard_err}. "
                        "Devuelve únicamente el objeto JSON corregido, sin texto adicional, "
                        f"con estas claves obligatorias: {sorted(_REQUIRED_KEYS)}."
                    ),
                },
            ]
            response = await llm.generate(
                system=prompt.system,
                messages=messages,
                temperature=0.2,
                max_tokens=1200,
            )
            return _parse_and_validate(response.text)

        except Exception as exc:
            logger.warning("generate_package LLM failed, using mock: %s", exc)
            await asyncio.sleep(0.5)
            return _MOCK_PACKAGE
