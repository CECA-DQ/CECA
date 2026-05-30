from src.services.segment_selection import _select_recurso


def _seg(t_start, t_end, score, hablante="plano_sala"):
    return {"t_start": t_start, "t_end": t_end, "max_score": score,
            "hablante": hablante, "cargo": "", "razon": ""}


def test_prefers_recurso_over_speaker():
    segs = [
        _seg(0.0, 8.0, 9, "Pedro Sánchez"),   # declaration — should be avoided
        _seg(10.0, 18.0, 2, "plano_sala"),    # recurso
        _seg(20.0, 28.0, 3, "desconocido"),   # recurso
    ]
    out = _select_recurso(segs, target_duration=30.0, max_segs=None)
    hablantes = {s["hablante"] for s in out}
    assert "Pedro Sánchez" not in hablantes
    assert len(out) == 2


def test_falls_back_to_lowest_score_when_no_recurso():
    segs = [
        _seg(0.0, 8.0, 9, "Pedro Sánchez"),
        _seg(10.0, 18.0, 7, "Pedro Sánchez"),
        _seg(20.0, 28.0, 8, "Pedro Sánchez"),
    ]
    out = _select_recurso(segs, target_duration=10.0, max_segs=None)
    assert len(out) == 1
    assert out[0]["max_score"] == 7


def test_no_overlap_and_chronological():
    segs = [_seg(0.0, 8.0, 2), _seg(4.0, 12.0, 2), _seg(20.0, 28.0, 2)]
    out = _select_recurso(segs, target_duration=60.0, max_segs=None)
    ts = [s["t_start"] for s in out]
    assert ts[0] == 0.0          # earliest non-overlapping clip kept
    assert 4.0 not in ts         # the overlapping 4-12 clip is skipped
    assert len(out) == 2         # 0-8 and 20-28


def test_partial_recurso_not_padded_with_speaker():
    # one short recurso clip + available speakers, target far from filled →
    # returns ONLY the recurso (no talking-head padding)
    segs = [
        _seg(0.0, 8.0, 2, "plano_sala"),     # the only recurso (8s)
        _seg(10.0, 18.0, 9, "Pedro Sánchez"),
        _seg(20.0, 28.0, 8, "Pedro Sánchez"),
    ]
    out = _select_recurso(segs, target_duration=30.0, max_segs=None)
    assert len(out) == 1
    assert out[0]["hablante"] == "plano_sala"
