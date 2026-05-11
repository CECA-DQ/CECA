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

_MIN_WORDS = 30
_MAX_WORDS = 300


class _GuardrailError(Exception):
    """Raised when the LLM output does not meet the guardrail constraints."""


def _validate(text: str) -> str:
    """Return the validated script or raise _GuardrailError."""
    cleaned = text.strip()
    if not cleaned:
        raise _GuardrailError("Empty response from LLM")
    word_count = len(cleaned.split())
    if word_count < _MIN_WORDS:
        raise _GuardrailError(f"Script too short: {word_count} words (minimum {_MIN_WORDS})")
    if word_count > _MAX_WORDS:
        raise _GuardrailError(f"Script too long: {word_count} words (maximum {_MAX_WORDS})")
    return cleaned


class WriteScriptStep(PipelineStep):
    """Generate the voiceover script that links selected segments.

    Guardrails:
    - Output must be between 30 and 300 words.
    - On validation failure: one retry with a correction instruction appended.
    - If LLM is unavailable or fails twice: falls back to mock script.
    """

    name = "write_script"
    description = "Write voiceover script linking the selected segments"

    async def execute(self, state: PipelineState) -> PipelineState:
        script = await self._generate(state)
        result = {
            "script": script,
            "word_count": len(script.split()),
            "source": "llm" if script != _MOCK_SCRIPT else "mock",
        }
        state.voiceover_script = script
        state.step_results[self.name] = result
        return state

    async def _generate(self, state: PipelineState) -> str:
        try:
            from src.adapters.llm.factory import get_llm_provider
            from src.services.prompt_store import get_prompt_store

            llm = get_llm_provider()
            prompt = get_prompt_store().render(
                "pipeline/write_script",
                transcript=state.transcript.get("full_text", ""),
                segments="\n".join(
                    f"- [{s['type']}] {s['reason']}" for s in state.selected_segments
                ),
            )
            messages = [{"role": "user", "content": prompt.user}]

            # Attempt 1
            response = await llm.generate(
                system=prompt.system,
                messages=messages,
                temperature=0.4,
                max_tokens=400,
            )
            try:
                return _validate(response.text)
            except _GuardrailError as guard_err:
                logger.warning(
                    "write_script guardrail failed (attempt 1): %s — retrying", guard_err
                )

            # Attempt 2 — add the bad response and a correction instruction
            messages = messages + [
                {"role": "assistant", "content": response.text},
                {
                    "role": "user",
                    "content": (
                        f"El texto anterior no cumple los requisitos: {guard_err}. "
                        "Reescríbelo respetando el límite de palabras y sin añadir nada más."
                    ),
                },
            ]
            response = await llm.generate(
                system=prompt.system,
                messages=messages,
                temperature=0.3,
                max_tokens=400,
            )
            return _validate(response.text)

        except Exception as exc:
            logger.warning("write_script LLM failed, using mock: %s", exc)
            await asyncio.sleep(0.3)
            return _MOCK_SCRIPT
