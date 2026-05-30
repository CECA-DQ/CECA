"""Transcript-driven candidate-moment detection.

Turns word-level transcript timestamps into a small ranked set of timestamps
worth scoring with vision, so frame sampling follows content instead of a
blind time grid. Pure functions, no I/O — fully unit-testable.
"""

import logging
import re

logger = logging.getLogger(__name__)

_SILENCE_GAP_S = 0.45          # gap (s) that ends a sentence unit
_SENT_END = (".", "?", "!")


def _finalize_unit(chunk: list[dict], bounded_by_pause: bool) -> dict:
    t_start = chunk[0]["start"]
    t_end = chunk[-1].get("end", chunk[-1]["start"])
    text = " ".join(w.get("word", "").strip() for w in chunk).strip()
    return {
        "t_start": t_start,
        "t_end": t_end,
        "text": text,
        "timestamp": round((t_start + t_end) / 2, 1),
        "bounded_by_pause": bounded_by_pause,
    }


def _build_units(words: list[dict]) -> list[dict]:
    """Group words into sentence-like units, breaking on sentence-final
    punctuation or a silence gap >= _SILENCE_GAP_S."""
    units: list[dict] = []
    chunk: list[dict] = []
    for i, w in enumerate(words):
        chunk.append(w)
        text = w.get("word", "").strip()
        ends_sentence = text.endswith(_SENT_END)
        if i + 1 < len(words):
            gap = words[i + 1]["start"] - w.get("end", w["start"])
        else:
            gap = 0.0
        last = i == len(words) - 1
        if ends_sentence or gap >= _SILENCE_GAP_S or last:
            units.append(_finalize_unit(chunk, bounded_by_pause=gap >= _SILENCE_GAP_S))
            chunk = []
    return units
