from src.services.segment_selection import _build_sentence_segments, select_segments


def _unit(t_start, t_end, text="x"):
    return {"t_start": t_start, "t_end": t_end, "text": text,
            "timestamp": round((t_start + t_end) / 2, 1), "bounded_by_pause": False}


def _frame(ts, score=8):
    return {"timestamp_s": ts, "puntuacion": score, "hablante": "Pedro Sánchez",
            "cargo_inferido": "Presidente", "razon_puntuacion": "cifra clave"}


def test_expands_whole_sentences_up_to_clip_s():
    units = [_unit(0.0, 4.0), _unit(4.2, 8.0), _unit(8.1, 12.0), _unit(12.1, 16.0)]
    segs = _build_sentence_segments([_frame(1.0)], units, clip_s=12.0, max_segment_s=20.0)
    assert len(segs) == 1
    assert segs[0]["t_start"] == 0.0
    assert segs[0]["t_end"] in (12.0, 16.0)
    assert (segs[0]["t_end"] - segs[0]["t_start"]) >= 12.0


def test_stops_at_speaker_turn_gap():
    units = [_unit(0.0, 4.0), _unit(4.2, 8.0), _unit(20.0, 24.0)]
    segs = _build_sentence_segments([_frame(1.0)], units, clip_s=30.0, max_segment_s=60.0)
    assert segs[0]["t_end"] == 8.0


def test_clamps_overlong_single_sentence():
    units = [_unit(0.0, 40.0)]
    segs = _build_sentence_segments([_frame(5.0)], units, clip_s=12.0, max_segment_s=20.0)
    assert segs[0]["t_start"] == 0.0 and segs[0]["t_end"] == 20.0


# ---------------------------------------------------------------------------
# End-to-end tests: select_segments routing (Task 3)
# ---------------------------------------------------------------------------

def _words_two_sentences():
    return [
        {"start": 0.0, "end": 0.5, "word": "Una"},
        {"start": 0.5, "end": 4.0, "word": "frase."},
        {"start": 4.2, "end": 4.7, "word": "Otra"},
        {"start": 4.7, "end": 8.0, "word": "frase."},
    ]


def test_nota_uses_sentence_aligned_cuts():
    # ts=3.0 is mid-sentence: a fixed window would start at 1.5 (3.0-1.5),
    # the sentence-aligned path starts at the enclosing sentence start (0.0).
    # Asserting 0.0 therefore proves the sentence path is taken.
    frames = [{"timestamp_s": 3.0, "puntuacion": 8, "hablante": "Sánchez"}]
    out = select_segments(frames, target_duration=60.0, words=_words_two_sentences(),
                          tipo_pieza="nota")
    assert out, "expected at least one segment"
    assert out[0]["t_start"] == 0.0


def test_broll_keeps_fixed_window():
    frames = [{"timestamp_s": 10.0, "puntuacion": 8, "hablante": "plano_sala"}]
    out = select_segments(frames, target_duration=60.0, words=_words_two_sentences(),
                          tipo_pieza="broll")
    assert any(abs(s["t_start"] - 8.5) < 0.6 for s in out)


def test_speech_type_without_words_falls_back():
    frames = [{"timestamp_s": 10.0, "puntuacion": 8, "hablante": "Sánchez"}]
    out = select_segments(frames, target_duration=60.0, words=[], tipo_pieza="nota")
    assert out and any(abs(s["t_start"] - 8.5) < 0.6 for s in out)


# ---------------------------------------------------------------------------
# Edge-case tests for _build_sentence_segments (Task 2 review items)
# ---------------------------------------------------------------------------

def test_build_sentence_segments_empty_candidates():
    units = [_unit(0.0, 4.0)]
    assert _build_sentence_segments([], units, clip_s=12.0, max_segment_s=20.0) == []


def test_build_sentence_segments_ts_after_all_units_uses_nearest():
    units = [_unit(0.0, 4.0), _unit(4.2, 8.0)]
    segs = _build_sentence_segments([_frame(100.0)], units, clip_s=12.0, max_segment_s=20.0)
    assert len(segs) == 1                      # nearest-unit fallback, not skipped
    assert segs[0]["t_start"] in (0.0, 4.2)


def test_build_sentence_segments_multiple_candidates():
    units = [_unit(0.0, 4.0), _unit(10.0, 14.0)]
    segs = _build_sentence_segments([_frame(1.0), _frame(11.0)], units, clip_s=4.0, max_segment_s=20.0)
    assert len(segs) == 2
    assert segs[0]["t_start"] == 0.0 and segs[1]["t_start"] == 10.0
