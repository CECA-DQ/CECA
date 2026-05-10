"""Unit tests for the STT adapter.

Run against MockSTTProvider only — no API calls, no audio files needed.
"""
import pytest

from src.adapters.stt.mock import MockSTTProvider


@pytest.fixture
def stt() -> MockSTTProvider:
    return MockSTTProvider()


async def test_transcribe_returns_transcript(stt: MockSTTProvider) -> None:
    result = await stt.transcribe(audio=b"fake-audio")
    assert result.language == "es"
    assert len(result.segments) > 0


async def test_full_text_is_concatenation_of_segments(stt: MockSTTProvider) -> None:
    result = await stt.transcribe(audio=b"fake-audio")
    for segment in result.segments:
        assert segment.text in result.full_text


async def test_segments_have_timestamps(stt: MockSTTProvider) -> None:
    result = await stt.transcribe(audio=b"fake-audio", with_timestamps=True)
    for segment in result.segments:
        assert segment.end > segment.start


async def test_diarization_populates_speaker(stt: MockSTTProvider) -> None:
    result = await stt.transcribe(audio=b"fake-audio", with_diarization=True)
    speakers = {s.speaker for s in result.segments}
    assert len(speakers) > 1  # mock has at least two speakers


async def test_without_diarization_speaker_is_none(stt: MockSTTProvider) -> None:
    result = await stt.transcribe(audio=b"fake-audio", with_diarization=False)
    assert all(s.speaker is None for s in result.segments)


async def test_language_override(stt: MockSTTProvider) -> None:
    result = await stt.transcribe(audio=b"fake-audio", language="en")
    assert result.language == "en"
