"""Multi-source (fuente_index) behaviour of segment selection.

These exercise the pure functions in segment_selection.py: that fuente_index
survives the segment builders, that overlap pruning is source-aware, and that
speech types fall back to per-frame selection when there is more than one source.
"""

from src.services.segment_selection import (
    _build_merged_segments,
    _build_per_frame_segments,
    _build_sentence_segments,
)
from src.services.transcript_units import build_sentence_units


def _frame(ts, score=5, fuente_index=0, hablante="plano_sala"):
    """A Gemini-scored frame as produced upstream (route adds fuente_index)."""
    return {
        "timestamp_s": ts,
        "puntuacion": score,
        "hablante": hablante,
        "cargo_inferido": "",
        "razon_puntuacion": "",
        "fuente_index": fuente_index,
    }


def _one_sentence_5_to_9():
    """One sentence, contiguous words 5.0→9.0s, no internal pause ≥0.35s
    (so _find_silences returns [] and Step C never snaps)."""
    return [
        {"word": "Una",       "start": 5.0, "end": 6.0},
        {"word": "frase",     "start": 6.0, "end": 7.0},
        {"word": "larga",     "start": 7.0, "end": 8.0},
        {"word": "completa.", "start": 8.0, "end": 9.0},
    ]


def test_per_frame_builder_preserves_fuente_index():
    frames = [_frame(10.0, fuente_index=0), _frame(20.0, fuente_index=1)]
    segs = _build_per_frame_segments(frames, clip_s=5.0)
    assert [s["fuente_index"] for s in segs] == [0, 1]


def test_per_frame_builder_defaults_fuente_index_to_zero():
    frame = {"timestamp_s": 10.0, "puntuacion": 5}  # no fuente_index key
    segs = _build_per_frame_segments([frame], clip_s=5.0)
    assert segs[0]["fuente_index"] == 0


def test_merged_builder_preserves_fuente_index():
    segs = _build_merged_segments([_frame(10.0, fuente_index=1)], max_segment_s=12.0)
    assert segs[0]["fuente_index"] == 1


def test_sentence_builder_preserves_fuente_index():
    units = build_sentence_units(_one_sentence_5_to_9())
    segs = _build_sentence_segments(
        [_frame(8.0, fuente_index=1, hablante="Juan")],
        units, clip_s=12.0, max_segment_s=12.0,
    )
    assert segs and segs[0]["fuente_index"] == 1


def test_sentence_builder_dedup_keeps_distinct_sources():
    # Two frames mapping to the same sentence span but different sources must
    # NOT collapse into one (dedup key includes fuente_index).
    units = build_sentence_units(_one_sentence_5_to_9())
    frames = [_frame(8.0, fuente_index=0), _frame(8.0, fuente_index=1)]
    segs = _build_sentence_segments(frames, units, clip_s=12.0, max_segment_s=12.0)
    assert sorted(s["fuente_index"] for s in segs) == [0, 1]
