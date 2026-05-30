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


from src.services.segment_selection import select_segments


def _frame(ts, score, hablante):
    return {"timestamp_s": ts, "puntuacion": score, "hablante": hablante}


def test_cola_routes_to_recurso_selection():
    frames = [
        _frame(2.0, 9, "Pedro Sánchez"),
        _frame(20.0, 2, "plano_sala"),
        _frame(40.0, 3, "plano_sala"),
    ]
    out = select_segments(frames, target_duration=60.0, words=[], tipo_pieza="cola")
    assert out
    assert all(s["hablante"] != "Pedro Sánchez" for s in out)


def test_nota_still_picks_high_score_speaker():
    frames = [
        _frame(2.0, 9, "Pedro Sánchez"),
        _frame(20.0, 2, "plano_sala"),
    ]
    out = select_segments(frames, target_duration=60.0, words=[], tipo_pieza="nota")
    assert any(s["hablante"] == "Pedro Sánchez" for s in out)


def test_nota_picks_highest_not_recurso_fallback():
    # two non-overlapping speaker frames; target fits only ~one 12s clip.
    # nota (_select_non_overlapping) picks the HIGHEST score (9);
    # _select_recurso would instead fall back to the LOWEST (6). Asserting 9 proves
    # nota is NOT routed through recurso.
    frames = [
        _frame(2.0, 9, "Pedro Sánchez"),
        _frame(40.0, 6, "Pedro Sánchez"),
    ]
    out = select_segments(frames, target_duration=12.0, words=[], tipo_pieza="nota")
    assert len(out) == 1
    assert out[0]["max_score"] == 9


def test_recurso_spread_not_frontloaded():
    # 60 recurso clips (8s) every 10s across a ~600s video; target 40s ≈ 5 clips.
    # Front-loaded selection would pick t=0..40 (last t_start ~32); even spreading
    # must reach well into the later part of the video.
    segs = [_seg(i * 10.0, i * 10.0 + 8.0, 2, "plano_sala") for i in range(60)]
    out = _select_recurso(segs, target_duration=40.0, max_segs=None)
    assert out, "expected clips"
    assert out[-1]["t_start"] >= 100.0   # spread, not clustered at the start
