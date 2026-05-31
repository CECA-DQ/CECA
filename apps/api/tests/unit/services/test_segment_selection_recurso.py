from src.services.segment_selection import _select_recurso, select_segments


def _seg(t_start, t_end, score, hablante="plano_sala"):
    return {"t_start": t_start, "t_end": t_end, "max_score": score,
            "hablante": hablante, "cargo": "", "razon": ""}


def _frame(ts, score, hablante):
    return {"timestamp_s": ts, "puntuacion": score, "hablante": hablante}


def test_recurso_preferred_when_it_fills_target():
    # recurso fills the target on its own → the speaker is NOT added (recurso preferred)
    segs = [
        _seg(0.0, 8.0, 9, "Pedro Sánchez"),   # declaration
        _seg(10.0, 18.0, 2, "plano_sala"),    # recurso
        _seg(20.0, 28.0, 3, "desconocido"),   # recurso
    ]
    out = _select_recurso(segs, target_duration=16.0, max_segs=None)
    hablantes = {s["hablante"] for s in out}
    assert "Pedro Sánchez" not in hablantes
    assert hablantes == {"plano_sala", "desconocido"}
    assert len(out) == 2


def test_no_recurso_fills_with_spread_speaker_takes():
    # no recurso at all → fill with speaker frames (muted), spread across the timeline
    segs = [
        _seg(0.0, 8.0, 9, "Pedro Sánchez"),
        _seg(10.0, 18.0, 7, "Pedro Sánchez"),
        _seg(20.0, 28.0, 8, "Pedro Sánchez"),
    ]
    out = _select_recurso(segs, target_duration=10.0, max_segs=None)
    assert len(out) == 1                       # ~one 8s clip fits a 10s target
    assert out[0]["hablante"] == "Pedro Sánchez"


def test_no_overlap_and_chronological():
    segs = [_seg(0.0, 8.0, 2), _seg(4.0, 12.0, 2), _seg(20.0, 28.0, 2)]
    out = _select_recurso(segs, target_duration=60.0, max_segs=None)
    ts = [s["t_start"] for s in out]
    assert ts[0] == 0.0          # earliest non-overlapping clip kept
    assert 4.0 not in ts         # the overlapping 4-12 clip is skipped
    assert len(out) == 2         # 0-8 and 20-28


def test_tops_up_with_muted_takes_when_recurso_short():
    # one recurso clip + speakers, target far from filled → tops up with muted
    # speaker takes (recurso still included) — NOT a single looped clip
    segs = [
        _seg(0.0, 8.0, 2, "plano_sala"),       # the only recurso
        _seg(40.0, 48.0, 9, "Pedro Sánchez"),
        _seg(80.0, 88.0, 8, "Pedro Sánchez"),
    ]
    out = _select_recurso(segs, target_duration=30.0, max_segs=None)
    assert len(out) == 3                                    # recurso + 2 muted takes
    assert any(s["hablante"] == "plano_sala" for s in out)  # recurso included
    total = sum(s["t_end"] - s["t_start"] for s in out)
    assert total >= 0.4 * 30.0                              # enough to avoid a loop


def test_low_recurso_no_loop_several_takes():
    # 1 recurso among many speakers in a long video, target 60 → fills with spread
    # muted takes so the total reaches the target (no loop) and there are several clips
    segs = [_seg(0.0, 5.0, 2, "plano_sala")] + [
        _seg(i * 30.0, i * 30.0 + 5.0, 9, "Pedro Sánchez") for i in range(1, 15)
    ]
    out = _select_recurso(segs, target_duration=60.0, max_segs=None)
    total = sum(s["t_end"] - s["t_start"] for s in out)
    assert total >= 0.4 * 60.0      # no _loop_to_duration
    assert len(out) > 1             # several distinct takes, not one looped clip


def test_cola_prefers_recurso_when_enough():
    # small target that recurso alone fills → cola excludes the speaker
    frames = [
        _frame(2.0, 9, "Pedro Sánchez"),
        _frame(20.0, 2, "plano_sala"),
        _frame(40.0, 3, "plano_sala"),
    ]
    out = select_segments(frames, target_duration=10.0, words=[], tipo_pieza="cola")
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
    # nota (_select_non_overlapping) picks the HIGHEST score (9). Asserting 9
    # proves nota is NOT routed through recurso.
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


def test_cola_cuts_are_about_5s():
    # recurso frames spaced 20s apart; cola clip length should be ~5s
    frames = [
        {"timestamp_s": 10.0, "puntuacion": 2, "hablante": "plano_sala"},
        {"timestamp_s": 30.0, "puntuacion": 2, "hablante": "plano_sala"},
    ]
    out = select_segments(frames, target_duration=60.0, words=[], tipo_pieza="cola")
    assert out
    for s in out:
        assert abs((s["t_end"] - s["t_start"]) - 5.0) < 0.01   # 5-second cuts
