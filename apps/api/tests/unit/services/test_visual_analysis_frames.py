import inspect
from src.services import visual_analysis


def test_extract_frames_at_exists_with_scale_default():
    fn = getattr(visual_analysis, "_extract_frames_at", None)
    assert fn is not None, "_extract_frames_at must exist"
    sig = inspect.signature(fn)
    assert "timestamps" in sig.parameters
    assert sig.parameters["scale"].default == "512:288"
