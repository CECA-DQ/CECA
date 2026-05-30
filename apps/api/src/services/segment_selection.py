"""Deterministic segment selection from scored visual frames.

Each piece type has its own selection strategy:
  - total:      1-2 cuts, highest journalistic score, same speaker preferred
  - teaser:     1-2 cuts, highest score only (hook moment)
  - promo:      up to 4 short cuts (5s), visually impactful
  - highlights: all high-score moments kept individually, chronological
  - cola/broll/off: per-frame 8s clips, any score >= 1 (b-roll diversity)
  - vtr/nota:   per-frame 12s soundbite clips, high-score frames, non-overlapping

No LLM calls are made here.
"""

import logging

from src.services.transcript_units import build_sentence_units

logger = logging.getLogger(__name__)

_MIN_SCORE_DECLARACION = 5    # minimum for speaker-focused pieces
_MERGE_GAP_S            = 10.0
_SILENCE_WINDOW         = 0.5
_MIN_SILENCE_GAP        = 0.35
_TURN_GAP_S             = 1.0    # transcript gap that likely marks a speaker turn; stop expanding
_SPEECH_TYPES           = {"total", "teaser", "promo", "vtr", "nota", "highlights"}
_RECURSO_TYPES          = {"cola", "broll"}   # b-roll recurso, no narration → recurso frames, muted
_RECURSO_MAX_SCORE      = 4               # Gemini band 1-4 = listening / wide / no active speech

# Per-type selection config
# per_frame=True  → one clip per sampled frame (no merging); all types now use this
# per_frame=False → merge nearby frames into soundbite windows (unused; kept for reference)
_TYPE_CONFIG: dict[str, dict] = {
    "total":      {"min_score": 7,  "max_segs": 2,    "clip_s":  12.0, "per_frame": True},
    "teaser":     {"min_score": 6,  "max_segs": 2,    "clip_s":   7.0, "per_frame": True},
    "promo":      {"min_score": 4,  "max_segs": 4,    "clip_s":   5.0, "per_frame": True},
    "highlights": {"min_score": 5,  "max_segs": None, "clip_s":  10.0, "per_frame": True},
    "cola":       {"min_score": 1,  "max_segs": None, "clip_s":   5.0, "per_frame": True},
    "broll":      {"min_score": 1,  "max_segs": None, "clip_s":   8.0, "per_frame": True},
    "off":        {"min_score": 1,  "max_segs": None, "clip_s":   8.0, "per_frame": True},
    "vtr":        {"min_score": 5,  "max_segs": None, "clip_s":  12.0, "per_frame": True},
    "nota":       {"min_score": 5,  "max_segs": None, "clip_s":  12.0, "per_frame": True},
}
_DEFAULT_CONFIG = {"min_score": 5, "max_segs": None, "clip_s": None, "per_frame": False}


def _find_silences(words: list[dict]) -> list[float]:
    silences: list[float] = []
    for i in range(len(words) - 1):
        gap_start = words[i].get("end", words[i].get("start", 0) + 0.2)
        gap_end   = words[i + 1].get("start", gap_start)
        if gap_end - gap_start >= _MIN_SILENCE_GAP:
            silences.append((gap_start + gap_end) / 2)
    return silences


def _snap_to_silence(t: float, silences: list[float], direction: str) -> float:
    if not silences:
        return t
    candidates = [
        s for s in silences
        if abs(s - t) <= _SILENCE_WINDOW
        and (direction == "before" and s <= t or direction == "after" and s >= t)
    ]
    if not candidates:
        candidates = [s for s in silences if abs(s - t) <= _SILENCE_WINDOW]
    return min(candidates, key=lambda s: abs(s - t)) if candidates else t


def _build_per_frame_segments(
    candidates: list[dict],
    clip_s: float,
) -> list[dict]:
    """One fixed-duration clip centred on each sampled frame."""
    segs = []
    for f in sorted(candidates, key=lambda f: f["timestamp_s"]):
        ts = f["timestamp_s"]
        segs.append({
            "t_start":   max(0.0, ts - 1.5),
            "t_end":     ts + clip_s - 1.5,
            "max_score": f.get("puntuacion", 0),
            "hablante":  f.get("hablante", "plano_sala"),
            "cargo":     f.get("cargo_inferido") or "",
            "razon":     f.get("razon_puntuacion", ""),
        })
    return segs


def _find_unit_for(ts: float, units: list[dict]) -> dict | None:
    """The sentence unit containing ts, else the nearest unit by midpoint."""
    for u in units:
        if u["t_start"] <= ts <= u["t_end"]:
            return u
    if not units:
        return None
    return min(units, key=lambda u: abs(u["timestamp"] - ts))


def _build_sentence_segments(
    candidates: list[dict],
    units: list[dict],
    clip_s: float,
    max_segment_s: float,
) -> list[dict]:
    """One clip per frame, expanded from the enclosing sentence through whole
    following sentences until ~clip_s, ending on a sentence boundary. Stops at a
    transcript gap larger than _TURN_GAP_S (likely speaker turn) and never
    exceeds max_segment_s.

    units must be sorted by t_start (build_sentence_units guarantees this)."""
    segs: list[dict] = []
    for f in sorted(candidates, key=lambda f: f["timestamp_s"]):
        ts = f["timestamp_s"]
        start_unit = _find_unit_for(ts, units)
        if start_unit is None:
            continue
        idx = units.index(start_unit)
        t_start = start_unit["t_start"]
        t_end = start_unit["t_end"]
        j = idx + 1
        while j < len(units):
            nxt = units[j]
            if nxt["t_start"] - t_end > _TURN_GAP_S:
                break
            if (t_end - t_start) >= clip_s:
                break
            if (nxt["t_end"] - t_start) > max_segment_s:
                break
            t_end = nxt["t_end"]
            j += 1
        if (t_end - t_start) > max_segment_s:
            t_end = t_start + max_segment_s
        segs.append({
            "t_start": t_start,
            "t_end": t_end,
            "max_score": f.get("puntuacion", 0),
            "hablante": f.get("hablante", "plano_sala"),
            "cargo": f.get("cargo_inferido") or "",
            "razon": f.get("razon_puntuacion", ""),
        })
    # Two frames inside the same sentence collapse to identical spans — keep the
    # highest-scoring one rather than emitting duplicates.
    unique: dict[tuple[float, float], dict] = {}
    for s in segs:
        key = (s["t_start"], s["t_end"])
        if key not in unique or s["max_score"] > unique[key]["max_score"]:
            unique[key] = s
    return sorted(unique.values(), key=lambda s: s["t_start"])


def _build_merged_segments(
    candidates: list[dict],
    max_segment_s: float,
) -> list[dict]:
    """Merge nearby speaker frames into soundbite windows (vtr/nota)."""
    segments: list[dict] = []
    current: dict | None = None

    for frame in sorted(candidates, key=lambda f: f["timestamp_s"]):
        ts    = frame["timestamp_s"]
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

    # Enforce max segment duration
    for seg in segments:
        if seg["t_end"] - seg["t_start"] > max_segment_s:
            seg["t_end"] = seg["t_start"] + max_segment_s

    return segments


def _select_non_overlapping(
    segments: list[dict],
    target_duration: float,
    max_segs: int | None,
) -> list[dict]:
    """Greedy score-descending selection that skips any segment overlapping an
    already-selected one.  Used for vtr/nota so 12s clips built from 5s-interval
    frames don't repeat the same footage.
    """
    by_score = sorted(segments, key=lambda s: s["max_score"], reverse=True)
    selected: list[dict] = []
    total = 0.0
    for seg in by_score:
        if max_segs is not None and len(selected) >= max_segs:
            break
        overlaps = any(
            seg["t_start"] < sel["t_end"] and seg["t_end"] > sel["t_start"]
            for sel in selected
        )
        if overlaps:
            continue
        dur = seg["t_end"] - seg["t_start"]
        if total + dur <= target_duration * 1.05:
            selected.append(seg)
            total += dur
    return sorted(selected, key=lambda s: s["t_start"])


def _select_recurso(
    segments: list[dict],
    target_duration: float,
    max_segs: int | None,
) -> list[dict]:
    """Select clips for cola/broll, spread across the timeline. Prefers recurso
    (non-speaker / low-score) frames, then tops up with the remaining frames —
    also spread — to fill the target, so a low-recurso source still yields several
    distinct takes instead of a single looped clip. cola/broll are rendered muted,
    so the topped-up speaker frames carry no audio."""
    def _is_recurso(s: dict) -> bool:
        return (
            s.get("hablante", "") in ("plano_sala", "desconocido", "")
            or s.get("max_score", 0) <= _RECURSO_MAX_SCORE
        )

    def _spread(clips: list[dict], budget: float) -> list[dict]:
        """Evenly sample clips across the timeline so the picks are distributed,
        not clustered at the start."""
        if not clips:
            return clips
        avg_dur = sum(c["t_end"] - c["t_start"] for c in clips) / len(clips)
        n_target = max(1, int(budget / max(avg_dur, 1.0)))
        if len(clips) <= n_target:
            return clips
        step = len(clips) / n_target
        return [clips[int(i * step)] for i in range(n_target)]

    recurso = sorted((s for s in segments if _is_recurso(s)), key=lambda s: s["t_start"])
    fallback = sorted((s for s in segments if not _is_recurso(s)), key=lambda s: s["t_start"])

    selected: list[dict] = []
    total = 0.0

    def _try_add(seg: dict) -> None:
        nonlocal total
        if max_segs is not None and len(selected) >= max_segs:
            return
        if any(seg["t_start"] < s["t_end"] and seg["t_end"] > s["t_start"] for s in selected):
            return
        dur = seg["t_end"] - seg["t_start"]
        if total + dur > target_duration * 1.05:
            return
        selected.append(seg)
        total += dur

    # Recurso first (preferred), spread across the timeline.
    for seg in _spread(recurso, target_duration):
        _try_add(seg)
    # Top up with the remaining (muted) frames — also spread — to fill the target
    # so a low-recurso source yields several distinct takes, never a single loop.
    if total < target_duration:
        for seg in _spread(fallback, target_duration - total):
            _try_add(seg)

    if segments and not selected:
        logger.warning(
            "_select_recurso: selected 0 clips from %d segments (target %.1fs)",
            len(segments), target_duration,
        )

    return sorted(selected, key=lambda s: s["t_start"])


def _select_for_total(
    segments: list[dict],
    target_duration: float,
) -> list[dict]:
    """Pick up to 2 cuts from the best-scoring speaker for a 'total' piece.

    Prefers both cuts from the same hablante. Falls back to top 2 by score
    if the primary speaker only has one high-scoring frame.
    """
    if not segments:
        return []

    by_score = sorted(segments, key=lambda s: s["max_score"], reverse=True)
    best      = by_score[0]
    speaker   = best["hablante"]
    selected  = [best]
    total     = best["t_end"] - best["t_start"]

    # Try to add a second cut from the same speaker
    for seg in by_score[1:]:
        if seg["hablante"] == speaker and speaker not in ("desconocido", "plano_sala", ""):
            dur = seg["t_end"] - seg["t_start"]
            if total + dur <= target_duration * 1.05:
                selected.append(seg)
                total += dur
                break

    # If same speaker had no second cut, try any second-best cut
    if len(selected) == 1:
        for seg in by_score[1:]:
            dur = seg["t_end"] - seg["t_start"]
            if total + dur <= target_duration * 1.05:
                selected.append(seg)
                break

    return sorted(selected, key=lambda s: s["t_start"])


def select_segments(
    scored_frames: list[dict],
    target_duration: float,
    words: list[dict] | None = None,
    min_segment_s: float = 5.0,
    max_segment_s: float = 12.0,
    tipo_pieza: str = "vtr",
) -> list[dict]:
    """Select the best segments from journalistic-scored frames.

    Behaviour varies by tipo_pieza — see _TYPE_CONFIG at the top of this file.
    Returns a list of segments: {"t_start", "t_end", "max_score", "hablante", ...}
    sorted chronologically.
    """
    if not scored_frames:
        return []

    cfg       = _TYPE_CONFIG.get(tipo_pieza, _DEFAULT_CONFIG)
    min_score = cfg["min_score"]
    max_segs  = cfg["max_segs"]
    clip_s    = cfg["clip_s"] or max_segment_s
    per_frame = cfg["per_frame"]

    silences = _find_silences(words or [])

    # Step A — filter dead/empty frames
    candidates = [f for f in scored_frames if f.get("puntuacion", 0) >= min_score]
    if not candidates:
        logger.warning(
            "No frames scored >= %d for tipo '%s' — using top fallback",
            min_score, tipo_pieza,
        )
        n_fallback = max(5, int(target_duration / clip_s) + 2)
        candidates = sorted(
            scored_frames, key=lambda f: f.get("puntuacion", 0), reverse=True
        )[:n_fallback]

    # Step B — build candidate segments
    # Speech/declaration types align cuts to whole sentences when a transcript
    # is available; everything else keeps the fixed-window / merged behaviour.
    units = build_sentence_units(words or []) if tipo_pieza in _SPEECH_TYPES else []
    if units:
        segments = _build_sentence_segments(candidates, units, clip_s, max_segment_s)
    elif per_frame:
        segments = _build_per_frame_segments(candidates, clip_s)
    else:
        segments = _build_merged_segments(candidates, max_segment_s)

    # Step C — snap cut boundaries to silence gaps and enforce min duration
    for seg in segments:
        seg["t_start"] = _snap_to_silence(seg["t_start"], silences, "before")
        seg["t_end"]   = _snap_to_silence(seg["t_end"],   silences, "after")
        if seg["t_end"] - seg["t_start"] < min_segment_s:
            seg["t_end"] = seg["t_start"] + min_segment_s

    # Step D — type-specific selection
    if tipo_pieza == "total":
        result = _select_for_total(segments, target_duration)
    elif tipo_pieza == "highlights":
        # Keep all high-score moments chronologically — no duration cap
        result = sorted(segments, key=lambda s: s["t_start"])
    elif tipo_pieza in ("vtr", "nota"):
        # 12s clips from 5s-interval frames overlap heavily — enforce no overlap
        result = _select_non_overlapping(segments, target_duration, max_segs)
    elif tipo_pieza in _RECURSO_TYPES:
        result = _select_recurso(segments, target_duration, max_segs)
    else:
        # teaser / promo / off → score-descending fill. NOTE: off is editorially recurso
        # (narrated b-roll) and should later use recurso selection too, but it carries a
        # voiceover (separate audio handling) and is deferred — see the cola spec.
        # Sort by score, fill up to target duration, respect max_segs
        by_score = sorted(segments, key=lambda s: s["max_score"], reverse=True)
        selected: list[dict] = []
        total = 0.0
        for seg in by_score:
            if max_segs is not None and len(selected) >= max_segs:
                break
            dur = seg["t_end"] - seg["t_start"]
            if total + dur <= target_duration * 1.05:
                selected.append(seg)
                total += dur
        result = sorted(selected, key=lambda s: s["t_start"])

    logger.info(
        "Segment selection [%s]: %d candidates → %d selected (%.1fs / %.1fs target)",
        tipo_pieza, len(segments), len(result),
        sum(s["t_end"] - s["t_start"] for s in result),
        target_duration,
    )
    return result
