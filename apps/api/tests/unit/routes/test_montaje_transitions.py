"""Crossfade transition filter builder + per-type transition decision."""

from src.routes.montaje import _build_xfade_filter


def test_two_clips_video_xfade_and_audio_acrossfade():
    f = _build_xfade_filter([5.0, 6.0], 0.4)
    # per-input normalization so xfade never errors on format/fps/sar/timebase
    assert "[0:v]fps=25,format=yuv420p,setsar=1,settb=AVTB[s0]" in f
    assert "[1:v]fps=25,format=yuv420p,setsar=1,settb=AVTB[s1]" in f
    # single video xfade at offset d0 - T, output [vout]
    assert "[s0][s1]xfade=transition=fade:duration=0.40:offset=4.60[vout]" in f
    # single audio acrossfade, output [aout]
    assert "[0:a][1:a]acrossfade=d=0.40[aout]" in f


def test_three_clips_cumulative_offsets():
    f = _build_xfade_filter([5.0, 6.0, 7.0], 0.4)
    assert "offset=4.60" in f                      # running(5.0) - 0.4
    assert "offset=10.20" in f                     # running(5+6-0.4=10.6) - 0.4
    assert f.count("xfade=") == 2
    assert f.count("acrossfade=") == 2
    assert "[vout]" in f and "[aout]" in f


def test_fewer_than_two_clips_returns_empty():
    assert _build_xfade_filter([5.0], 0.4) == ""
    assert _build_xfade_filter([], 0.4) == ""
    assert _build_xfade_filter([5.0, 6.0], 0.0) == ""


def test_transition_clamped_to_half_shortest_clip():
    # shortest clip 0.5s → T clamps to 0.25 so the xfade offset never goes negative
    f = _build_xfade_filter([0.5, 0.5], 0.4)
    assert "duration=0.25" in f
    assert "offset=0.25" in f                      # 0.5 - 0.25


def test_mute_emits_video_only_no_acrossfade():
    f = _build_xfade_filter([5.0, 6.0], 0.4, mute=True)
    assert "xfade=" in f and "[vout]" in f
    assert "acrossfade=" not in f
    assert "[aout]" not in f
