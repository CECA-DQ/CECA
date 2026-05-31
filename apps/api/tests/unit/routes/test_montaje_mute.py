from pathlib import Path
from src.routes.montaje import _normalize_cmd


def test_mute_drops_audio():
    cmd = _normalize_cmd("ffmpeg", Path("in.mp4"), 0.0, 8.0, Path("out.mp4"), mute=True)
    assert "-an" in cmd
    assert "-c:a" not in cmd


def test_no_mute_keeps_audio():
    cmd = _normalize_cmd("ffmpeg", Path("in.mp4"), 0.0, 8.0, Path("out.mp4"), mute=False)
    assert "-c:a" in cmd
    assert "-an" not in cmd


def test_no_t_flag_when_t_end_none():
    cmd = _normalize_cmd("ffmpeg", Path("in.mp4"), 0.0, None, Path("out.mp4"), mute=True)
    assert "-t" not in cmd


def test_an_is_an_output_option_after_input():
    cmd = _normalize_cmd("ffmpeg", Path("in.mp4"), 0.0, 8.0, Path("out.mp4"), mute=True)
    assert cmd.index("-an") > cmd.index("-i")   # -an after -i = output option (correct)
