"""Unit tests for the TTS adapter.

Run against MockTTSProvider only — no API calls, no audio generation needed.
"""
import pytest

from src.adapters.tts.mock import MockTTSProvider


@pytest.fixture
def tts() -> MockTTSProvider:
    return MockTTSProvider()


async def test_synthesize_returns_result(tts: MockTTSProvider) -> None:
    result = await tts.synthesize(text="Hola mundo.", voice_id="voice-es-1")
    assert result.voice_id == "voice-es-1"


async def test_synthesize_estimates_duration(tts: MockTTSProvider) -> None:
    # 150 words → ~60 seconds at 150 wpm
    text = " ".join(["palabra"] * 150)
    result = await tts.synthesize(text=text, voice_id="voice-es-1")
    assert 55.0 <= result.duration_seconds <= 65.0


async def test_synthesize_short_text_has_short_duration(tts: MockTTSProvider) -> None:
    result = await tts.synthesize(text="Hola.", voice_id="voice-es-1")
    assert result.duration_seconds < 5.0


async def test_estimate_cost_mock_is_zero(tts: MockTTSProvider) -> None:
    assert tts.estimate_cost(1000) == 0.0
