from src.services.candidate_moments import _build_units, _score_unit, find_candidate_moments


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


def _unit(text, bounded_by_pause=False):
    return {"text": text, "bounded_by_pause": bounded_by_pause,
            "t_start": 0.0, "t_end": 1.0, "timestamp": 0.5}


def test_score_rewards_numbers():
    with_num = _score_unit(_unit("inversión de 2000 millones de euros este año"), [])
    without = _score_unit(_unit("vamos a hablar de varias cosas importantes hoy"), [])
    assert with_num > without


def test_score_rewards_tema_keywords():
    s = _score_unit(_unit("el plan ferroviario nacional avanza con fuerza"), ["ferroviario"])
    base = _score_unit(_unit("el plan nacional avanza con mucha fuerza"), ["ferroviario"])
    assert s > base


def test_build_units_empty_returns_empty():
    assert _build_units([]) == []


def test_respects_budget_and_spacing():
    words = []
    t = 0.0
    for i in range(200):
        words.append(_w(t, t + 0.4, f"palabra{i}" + ("." if i % 5 == 4 else "")))
        t += 0.5
    out = find_candidate_moments(words, [], duration=t, tema="", budget=25)
    assert len(out) <= 25
    ts = [c["timestamp"] for c in out]
    assert ts == sorted(ts)
    assert all(b - a >= 4.0 for a, b in zip(ts, ts[1:]))


def test_grid_floor_fills_sparse_transcript():
    words = [_w(2.0, 2.3, "hola."), _w(2.3, 2.6, "qué."), _w(2.6, 2.9, "tal.")]
    out = find_candidate_moments(words, [], duration=120.0, tema="", budget=10)
    assert any(c["source"] == "grid" for c in out)


def test_empty_input_returns_empty():
    assert find_candidate_moments([], [], duration=0.0) == []
    assert find_candidate_moments([], [], duration=100.0) == []
