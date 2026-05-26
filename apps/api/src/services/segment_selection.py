"""Deterministic segment selection from scored visual frames.

Replaces LLM-based cut selection. A human TV editor watches first,
then cuts — this module does the same: it reads the journalistic scores
produced by visual_analysis.py and picks the best segments using
a rule-based algorithm.

No LLM calls are made here.
"""

import logging

logger = logging.getLogger(__name__)

_MIN_SCORE       = 5     # frames below this are dead moments, never cut there
_MERGE_GAP_S     = 10.0  # frames within this gap form one segment
_SILENCE_WINDOW  = 0.5   # seconds to search for a silence boundary
_MIN_SILENCE_GAP = 0.35  # a pause >= this between words counts as silence


def _find_silences(words: list[dict]) -> list[float]:
    """Return midpoints of gaps >= _MIN_SILENCE_GAP between consecutive words."""
    silences: list[float] = []
    for i in range(len(words) - 1):
        gap_start = words[i].get("end",   words[i].get("start", 0) + 0.2)
        gap_end   = words[i + 1].get("start", gap_start)
        if gap_end - gap_start >= _MIN_SILENCE_GAP:
            silences.append((gap_start + gap_end) / 2)
    return silences


def _snap_to_silence(t: float, silences: list[float], direction: str) -> float:
    """Snap t to the nearest silence within _SILENCE_WINDOW seconds.

    direction="before" prefers silences <= t.
    direction="after"  prefers silences >= t.
    Falls back to t if no silence found in the window.
    """
    if not silences:
        return t
    candidates = [
        s for s in silences
        if abs(s - t) <= _SILENCE_WINDOW
        and (direction == "before" and s <= t or direction == "after" and s >= t)
    ]
    if not candidates:
        # Relax to nearest silence regardless of direction
        candidates = [s for s in silences if abs(s - t) <= _SILENCE_WINDOW]
    return min(candidates, key=lambda s: abs(s - t)) if candidates else t


def select_segments(
    scored_frames: list[dict],
    target_duration: float,
    words: list[dict] | None = None,
    min_segment_s: float = 5.0,
    max_segment_s: float = 12.0,
    tipo_pieza: str = "vtr",
) -> list[dict]:
    """Select the best segments from journalistic-scored frames.

    Steps:
      A. Reject frames with score < _MIN_SCORE (dead moments).
      B. Group consecutive high-score frames into candidate segments.
      C. Snap cut boundaries to silence gaps in the audio.
      D. Trim to target_duration, keeping highest-scoring segments first.
      E. Re-sort chronologically for the final edit.

    Returns a list of segments:
      {"t_start", "t_end", "max_score", "hablante", "cargo", "razon"}
    """
    if not scored_frames:
        return []

    silences = _find_silences(words or [])

    # Broll/cola pieces have no speaker requirement — lower the score threshold
    # so visually interesting shots (score 2-4) are included.
    _BROLL_TYPES = {"cola", "broll", "off", "promo", "teaser"}
    min_score = 2 if tipo_pieza in _BROLL_TYPES else _MIN_SCORE

    # Step A — filter dead moments
    candidates = [f for f in scored_frames if f.get("puntuacion", 0) >= min_score]
    if not candidates:
        logger.warning("No frames scored >= %d — using top fallback", min_score)
        # Pick enough frames to cover target_duration without looping
        n_fallback = max(5, int(target_duration / max_segment_s) + 2)
        candidates = sorted(scored_frames, key=lambda f: f.get("puntuacion", 0), reverse=True)[:n_fallback]

    # Step B — build candidate segments
    segments: list[dict] = []

    if tipo_pieza in _BROLL_TYPES:
        # B-roll/cola: one independent clip per frame, no merging.
        # Frames are sampled every 5s so merging them collapses the whole video
        # into a single 12s segment. Instead, give step D many short clips to
        # choose from so it can fill the target duration from diverse moments.
        clip_s = min(max_segment_s, 8.0)
        for frame in sorted(candidates, key=lambda f: f["timestamp_s"]):
            ts = frame["timestamp_s"]
            segments.append({
                "t_start":   max(0.0, ts - 1.5),
                "t_end":     ts + clip_s - 1.5,
                "max_score": frame.get("puntuacion", 0),
                "hablante":  frame.get("hablante", "plano_sala"),
                "cargo":     frame.get("cargo_inferido") or "",
                "razon":     frame.get("razon_puntuacion", ""),
            })
    else:
        # Declaracion types: merge nearby frames into a single soundbite window
        current: dict | None = None
        for frame in sorted(candidates, key=lambda f: f["timestamp_s"]):
            ts = frame["timestamp_s"]
            score = frame.get("puntuacion", 0)

            if current is None:
                current = {
                    "t_start":   max(0.0, ts - 3.0),
                    "t_end":     ts + 8.0,
                    "max_score": score,
                    "hablante":  frame.get("hablante", "desconocido"),
                    "cargo":     frame.get("cargo_inferido") or "",
                    "razon":     frame.get("razon_puntuacion", ""),
                }
            elif ts - current["t_end"] < _MERGE_GAP_S:
                current["t_end"] = ts + 8.0
                if score > current["max_score"]:
                    current["max_score"] = score
                    current["hablante"]  = frame.get("hablante", current["hablante"])
                    current["cargo"]     = frame.get("cargo_inferido") or current["cargo"]
                    current["razon"]     = frame.get("razon_puntuacion", current["razon"])
            else:
                segments.append(current)
                current = {
                    "t_start":   max(0.0, ts - 3.0),
                    "t_end":     ts + 8.0,
                    "max_score": score,
                    "hablante":  frame.get("hablante", "desconocido"),
                    "cargo":     frame.get("cargo_inferido") or "",
                    "razon":     frame.get("razon_puntuacion", ""),
                }

        if current:
            segments.append(current)

    # Step C — snap boundaries to silence gaps
    for seg in segments:
        seg["t_start"] = _snap_to_silence(seg["t_start"], silences, "before")
        seg["t_end"]   = _snap_to_silence(seg["t_end"],   silences, "after")
        # Enforce min/max segment duration
        dur = seg["t_end"] - seg["t_start"]
        if dur < min_segment_s:
            seg["t_end"] = seg["t_start"] + min_segment_s
        if dur > max_segment_s:
            seg["t_end"] = seg["t_start"] + max_segment_s

    # Step D — keep highest-scoring until target duration is filled
    by_score = sorted(segments, key=lambda s: s["max_score"], reverse=True)
    selected: list[dict] = []
    total = 0.0
    for seg in by_score:
        dur = seg["t_end"] - seg["t_start"]
        if total + dur <= target_duration * 1.05:  # 5% tolerance
            selected.append(seg)
            total += dur

    # Step E — re-sort chronologically
    result = sorted(selected, key=lambda s: s["t_start"])

    logger.info(
        "Segment selection: %d candidates → %d selected (%.1fs / %.1fs target)",
        len(segments), len(result), total, target_duration,
    )
    return result
