import logging

from src.adapters.storage.factory import get_storage_adapter
from src.adapters.tts.factory import get_tts_provider
from src.config import settings
from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)


class GenerateVoiceoverStep(PipelineStep):
    """Send the voiceover script to the TTS provider and store the audio."""

    name = "generate_voiceover"
    description = "Script → audio via TTS"

    async def execute(self, state: PipelineState) -> PipelineState:
        script = state.voiceover_script
        audio_key = f"audio/{state.project_id}/voiceover.mp3"

        if not script:
            logger.warning("No voiceover script — skipping TTS synthesis")
            state.voiceover_audio_key = audio_key
            state.step_results[self.name] = {"audio_key": audio_key, "skipped": True}
            return state

        try:
            tts = get_tts_provider()
            voice_id = settings.tts_default_voice_id
            result = await tts.synthesize(text=script, voice_id=voice_id, language="es")

            storage = get_storage_adapter()
            await storage.upload(audio_key, result.audio, "audio/mpeg")

            logger.info(
                "Voiceover synthesized: %.1fs, %d bytes → %s",
                result.duration_seconds,
                len(result.audio),
                audio_key,
            )
            state.voiceover_audio_key = audio_key
            state.step_results[self.name] = {
                "audio_key": audio_key,
                "duration_seconds": result.duration_seconds,
                "voice_id": result.voice_id,
                "provider": settings.tts_provider,
                "size_bytes": len(result.audio),
            }
        except Exception as exc:
            logger.warning(
                "TTS synthesis failed (%s: %s) — using mock audio key",
                type(exc).__name__,
                exc,
            )
            state.voiceover_audio_key = audio_key
            state.step_results[self.name] = {
                "audio_key": audio_key,
                "provider": "mock_fallback",
                "error": str(exc),
            }

        return state
