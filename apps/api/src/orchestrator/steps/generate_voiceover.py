import asyncio

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep


class GenerateVoiceoverStep(PipelineStep):
    """Send the voiceover script to the TTS provider and store the audio."""

    name = "generate_voiceover"
    description = "Script → audio via TTS"

    async def execute(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(2.0)  # simulate ElevenLabs API call

        audio_key = f"audio/{state.project_id}/voiceover.mp3"
        result = {
            "audio_key": audio_key,
            "duration_seconds": 48.3,
            "voice_id": "mock_voice_es_neutral",
            "provider": "mock",
        }
        state.voiceover_audio_key = audio_key
        state.step_results[self.name] = result
        return state
