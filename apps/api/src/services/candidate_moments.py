"""Transcript-driven candidate-moment detection.

Turns word-level transcript timestamps into a small ranked set of timestamps
worth scoring with vision, so frame sampling follows content instead of a
blind time grid. Pure functions, no I/O — fully unit-testable.
"""

import logging
import re

from src.services.transcript_units import build_sentence_units

logger = logging.getLogger(__name__)

_NUM_RE = re.compile(r"\d")
_MIN_WORDS = 4
_MAX_WORDS = 60
_MIN_SPACING_S = 4.0
_GRID_FRACTION = 0.2


def _score_unit(unit: dict, tema_keywords: list[str]) -> float:
    """Cheap journalistic-value heuristic. Higher = more worth scoring visually."""
    text = unit["text"]
    n_words = len(text.split())
    score = 0.0
    if _NUM_RE.search(text):
        score += 2.0
    if tema_keywords:
        low = text.lower()
        score += float(sum(1 for k in tema_keywords if k in low))
    if _MIN_WORDS <= n_words <= _MAX_WORDS:
        score += 1.0
    if unit.get("bounded_by_pause"):
        score += 1.0
    return score


def _far_enough(ts: float, chosen: list[dict]) -> bool:
    """Check if timestamp is at least _MIN_SPACING_S away from all chosen moments."""
    return all(abs(ts - c["timestamp"]) >= _MIN_SPACING_S for c in chosen)


def find_candidate_moments(
    words: list[dict],
    segments: list[dict],
    duration: float,
    tema: str = "",
    budget: int = 25,
) -> list[dict]:
    """Return up to `budget` timestamps worth scoring, chronologically sorted.

    Each item: {"timestamp", "t_start", "t_end", "text", "score", "source"}.
    `source` is "sentence" (content-driven) or "grid" (coverage floor).
    Returns [] on empty input or any failure (caller falls back to grid).
    """
    try:
        if not words or duration <= 0:
            return []
        units = build_sentence_units(words)
        if not units:
            return []

        tema_keywords = [t for t in re.findall(r"\w+", tema.lower()) if len(t) > 3]  # skip short tokens to avoid substring noise
        for u in units:
            u["score"] = _score_unit(u, tema_keywords)
            u["source"] = "sentence"

        n_sentence = max(1, int(budget * (1 - _GRID_FRACTION)))
        chosen: list[dict] = []
        for u in sorted(units, key=lambda x: x["score"], reverse=True):
            if len(chosen) >= n_sentence:
                break
            if _far_enough(u["timestamp"], chosen):
                chosen.append(u)

        n_grid = max(0, budget - len(chosen))
        if n_grid > 0:
            step = duration / (n_grid + 1)
            for k in range(1, n_grid + 1):
                ts = round(step * k, 1)
                if _far_enough(ts, chosen):
                    chosen.append({
                        "timestamp": ts,
                        "t_start": max(0.0, ts - 2.0),
                        "t_end": min(ts + 2.0, duration),
                        "text": "",
                        "score": 0.0,
                        "source": "grid",
                    })

        return sorted(chosen, key=lambda c: c["timestamp"])[:budget]
    except Exception as exc:
        logger.warning("find_candidate_moments failed: %s", exc)
        return []
