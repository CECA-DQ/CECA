from .base import TTSProvider, SynthesisResult

# Approximate words-per-minute for cost/duration estimation in tests
_WPM = 150
_COST_PER_CHAR = 0.0


class MockTTSProvider(TTSProvider):
    """Returns silent audio bytes. No external calls — for tests and demos."""

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        language: str = "es",
    ) -> SynthesisResult:
        word_count = len(text.split())
        duration = (word_count / _WPM) * 60

        return SynthesisResult(
            audio=b"",          # empty — no real audio in tests
            duration_seconds=round(duration, 2),
            voice_id=voice_id,
        )

    def estimate_cost(self, character_count: int) -> float:
        return _COST_PER_CHAR * character_count
