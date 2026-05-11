from elevenlabs.client import AsyncElevenLabs

from .base import TTSProvider, SynthesisResult

# ElevenLabs pricing: ~$0.30 per 1000 characters (Creator plan, May 2026)
_COST_PER_CHAR = 0.00030


class ElevenLabsProvider(TTSProvider):
    """Text-to-speech via ElevenLabs API.

    Returns mp3 audio bytes ready to be stored and mixed into the final video.

    Required settings:
        ELEVENLABS_API_KEY
    """

    def __init__(self, api_key: str) -> None:
        self._client = AsyncElevenLabs(api_key=api_key)

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        language: str = "es",
    ) -> SynthesisResult:
        audio_chunks: list[bytes] = []
        async for chunk in self._client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        ):
            if chunk:
                audio_chunks.append(chunk)
        audio = b"".join(audio_chunks)

        # ElevenLabs does not return duration — estimate from character count
        duration = len(text.split()) / 150 * 60

        return SynthesisResult(
            audio=audio,
            duration_seconds=round(duration, 2),
            voice_id=voice_id,
        )

    def estimate_cost(self, character_count: int) -> float:
        return character_count * _COST_PER_CHAR
