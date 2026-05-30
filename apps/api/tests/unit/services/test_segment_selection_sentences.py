from src.services.segment_selection import _build_sentence_segments


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
