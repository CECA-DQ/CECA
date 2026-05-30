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
    assert ts == sorted(ts)
    assert not (0.0 in ts and 4.0 in ts)
