from src.services.candidate_moments import _build_units


def _w(start, end, word):
    return {"start": start, "end": end, "word": word}


def test_build_units_splits_on_sentence_punctuation():
    words = [_w(0.0, 0.4, "Hola"), _w(0.4, 0.9, "mundo."),
             _w(1.0, 1.4, "Otra"), _w(1.4, 1.9, "frase.")]
    units = _build_units(words)
    assert len(units) == 2
    assert units[0]["text"] == "Hola mundo."
    assert units[0]["t_start"] == 0.0 and units[0]["t_end"] == 0.9


def test_build_units_splits_on_silence_gap():
    # 1.0s gap between the two words → two units even without punctuation
    words = [_w(0.0, 0.4, "uno"), _w(1.4, 1.8, "dos")]
    units = _build_units(words)
    assert len(units) == 2
    assert units[0]["bounded_by_pause"] is True
