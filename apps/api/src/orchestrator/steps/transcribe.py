import asyncio

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep


class TranscribeStep(PipelineStep):
    """Transcribe audio with per-word timestamps and speaker diarization."""

    name = "transcribe"
    description = "Audio → text with timestamps and speaker detection"

    async def execute(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(2.0)  # simulate Whisper API call

        segments = [
            {"start": 0.0,   "end": 5.2,  "speaker": "SPEAKER_00", "text": "Buenos días a todos. Vamos a comenzar la rueda de prensa sobre el nuevo plan de infraestructuras."},
            {"start": 5.4,   "end": 14.1, "speaker": "SPEAKER_00", "text": "Este plan supone una inversión de dos mil millones de euros en los próximos cuatro años para modernizar la red ferroviaria nacional."},
            {"start": 14.5,  "end": 22.8, "speaker": "SPEAKER_00", "text": "El objetivo es conectar las principales ciudades con trenes de alta velocidad y reducir los tiempos de viaje a la mitad."},
            {"start": 23.1,  "end": 31.4, "speaker": "SPEAKER_01", "text": "¿Puede explicar cómo se va a financiar este proyecto y qué impacto tendrá en el déficit público?"},
            {"start": 32.0,  "end": 45.7, "speaker": "SPEAKER_00", "text": "La financiación proviene de fondos europeos NextGenerationEU, que cubren el sesenta por ciento, y del presupuesto nacional el resto. No se prevé un aumento del déficit."},
            {"start": 46.2,  "end": 58.3, "speaker": "SPEAKER_01", "text": "¿Cuántos empleos directos e indirectos generará la obra durante la fase de construcción?"},
            {"start": 59.0,  "end": 72.1, "speaker": "SPEAKER_00", "text": "Las estimaciones apuntan a unos treinta y cinco mil empleos directos durante los cuatro años de construcción y unos ochenta mil empleos indirectos en la cadena de suministro."},
            {"start": 73.0,  "end": 84.5, "speaker": "SPEAKER_02", "text": "¿Qué criterios se han utilizado para seleccionar los tramos prioritarios del proyecto?"},
            {"start": 85.1,  "end": 102.4,"speaker": "SPEAKER_00", "text": "Se han priorizado los corredores con mayor densidad de tráfico y mayor impacto económico. El corredor mediterráneo y el corredor atlántico son los primeros en iniciar obras."},
        ]

        full_text = " ".join(s["text"] for s in segments)
        result = {
            "language": "es",
            "segments": segments,
            "full_text": full_text,
            "total_segments": len(segments),
            "speakers_detected": 3,
        }
        state.transcript = result
        state.step_results[self.name] = result
        return state
