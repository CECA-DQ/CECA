# Sentence-aligned Cut Boundaries — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For speech/declaration piece types, build each cut as whole sentences expanded up to the target clip duration (ending on a sentence boundary), instead of a fixed window around the frame timestamp.

**Architecture:** Extract sentence-unit splitting into a shared `transcript_units` module (DRY with `candidate_moments`). In `segment_selection`, recompute sentence units from the `words` it already receives and, for speech types, build clips by expanding from the enclosing sentence through whole following sentences until ~`clip_s`, stopping at a likely speaker turn (`_TURN_GAP_S`). B-roll types and the no-transcript case keep the existing fixed-window builder.

**Tech Stack:** Python 3.12, pytest. Pure functions, no I/O.

**Spec:** `docs/superpowers/specs/2026-05-30-cut-sentence-alignment-design.md`

---

## File Structure

- Create: `src/services/transcript_units.py` — `build_sentence_units(words)` (moved from `candidate_moments`)
- Modify: `src/services/candidate_moments.py` — import the shared helper; drop the local copy
- Modify: `src/services/segment_selection.py` — `_build_sentence_segments`, `_find_unit_for`, constants, branch in `select_segments`
- Create: `tests/unit/services/test_transcript_units.py` — moved unit tests
- Modify: `tests/unit/services/test_candidate_moments.py` — drop the moved tests + import
- Create: `tests/unit/services/test_segment_selection_sentences.py` — new behavior tests

Run all commands from `apps/api/`.

---

### Task 1: Extract `build_sentence_units` into a shared module (pure refactor, stay green)

**Files:**
- Create: `apps/api/src/services/transcript_units.py`
- Modify: `apps/api/src/services/candidate_moments.py`
- Create: `apps/api/tests/unit/services/test_transcript_units.py`
- Modify: `apps/api/tests/unit/services/test_candidate_moments.py`

- [ ] **Step 1: Create `transcript_units.py`**

```python
# src/services/transcript_units.py
"""Shared transcript helper: split word-level timestamps into sentence units.

Used by candidate_moments (to pick moments worth scoring) and segment_selection
(to align cut boundaries to sentences). Pure functions, no I/O.
"""

_SILENCE_GAP_S = 0.45          # gap (s) that ends a sentence unit
_SENT_END = (".", "?", "!")


def _finalize_unit(chunk: list[dict], bounded_by_pause: bool) -> dict:
    """Collapse a word chunk into a unit dict."""
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


def build_sentence_units(words: list[dict]) -> list[dict]:
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
```

- [ ] **Step 2: Create `tests/unit/services/test_transcript_units.py` (moved tests) and run**

```python
from src.services.transcript_units import build_sentence_units


def _w(start, end, word):
    return {"start": start, "end": end, "word": word}


def test_build_units_splits_on_sentence_punctuation():
    words = [_w(0.0, 0.4, "Hola"), _w(0.4, 0.9, "mundo."),
             _w(1.0, 1.4, "Otra"), _w(1.4, 1.9, "frase.")]
    units = build_sentence_units(words)
    assert len(units) == 2
    assert units[0]["text"] == "Hola mundo."
    assert units[0]["t_start"] == 0.0 and units[0]["t_end"] == 0.9


def test_build_units_splits_on_silence_gap():
    words = [_w(0.0, 0.4, "uno"), _w(1.4, 1.8, "dos")]
    units = build_sentence_units(words)
    assert len(units) == 2
    assert units[0]["bounded_by_pause"] is True


def test_build_units_empty_returns_empty():
    assert build_sentence_units([]) == []
```

Run: `uv run pytest tests/unit/services/test_transcript_units.py -v`
Expected: PASS (3 passed)

- [ ] **Step 3: Point `candidate_moments.py` at the shared helper**

In `src/services/candidate_moments.py`:
1. Add to the imports (top of file, after `import re`): `from src.services.transcript_units import build_sentence_units`
2. Delete the local `_finalize_unit` and `_build_units` functions.
3. Delete the now-unused module constants `_SILENCE_GAP_S = 0.45` and `_SENT_END = (".", "?", "!")`. (Keep `_NUM_RE`, `_MIN_WORDS`, `_MAX_WORDS`, `_MIN_SPACING_S`, `_GRID_FRACTION` — still used.)
4. In `find_candidate_moments`, change the call `units = _build_units(words)` to `units = build_sentence_units(words)`.

- [ ] **Step 4: Remove the moved tests from `test_candidate_moments.py`**

In `tests/unit/services/test_candidate_moments.py`:
1. Change the top import from `from src.services.candidate_moments import _build_units, _score_unit, find_candidate_moments` to `from src.services.candidate_moments import _score_unit, find_candidate_moments`.
2. Delete the three tests `test_build_units_splits_on_sentence_punctuation`, `test_build_units_splits_on_silence_gap`, and `test_build_units_empty_returns_empty` (now in `test_transcript_units.py`). Keep `_w` (still used by other tests) and all `_score_unit`/`find_candidate_moments` tests.

- [ ] **Step 5: Run the full unit suite to confirm the refactor is green**

Run: `uv run pytest tests/unit -q`
Expected: PASS (same count as before, just relocated tests)

- [ ] **Step 6: Commit**

```bash
git add src/services/transcript_units.py src/services/candidate_moments.py tests/unit/services/test_transcript_units.py tests/unit/services/test_candidate_moments.py
git commit -m "refactor(services): extract build_sentence_units into shared transcript_units"
```

---

### Task 2: `_build_sentence_segments` in segment_selection

**Files:**
- Modify: `apps/api/src/services/segment_selection.py`
- Test: `apps/api/tests/unit/services/test_segment_selection_sentences.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_segment_selection_sentences.py
from src.services.segment_selection import _build_sentence_segments


def _unit(t_start, t_end, text="x"):
    return {"t_start": t_start, "t_end": t_end, "text": text,
            "timestamp": round((t_start + t_end) / 2, 1), "bounded_by_pause": False}


def _frame(ts, score=8):
    return {"timestamp_s": ts, "puntuacion": score, "hablante": "Pedro Sánchez",
            "cargo_inferido": "Presidente", "razon_puntuacion": "cifra clave"}


def test_expands_whole_sentences_up_to_clip_s():
    # 4s sentences; clip_s=12 → expect ~3 sentences, ending on a boundary
    units = [_unit(0.0, 4.0), _unit(4.2, 8.0), _unit(8.1, 12.0), _unit(12.1, 16.0)]
    segs = _build_sentence_segments([_frame(1.0)], units, clip_s=12.0, max_segment_s=20.0)
    assert len(segs) == 1
    assert segs[0]["t_start"] == 0.0          # starts at the enclosing sentence start
    assert segs[0]["t_end"] in (12.0, 16.0)   # ends on a sentence boundary, ~clip_s
    assert (segs[0]["t_end"] - segs[0]["t_start"]) >= 12.0


def test_stops_at_speaker_turn_gap():
    # big gap (>1s) after the 2nd sentence → must not merge across it
    units = [_unit(0.0, 4.0), _unit(4.2, 8.0), _unit(20.0, 24.0)]
    segs = _build_sentence_segments([_frame(1.0)], units, clip_s=30.0, max_segment_s=60.0)
    assert segs[0]["t_end"] == 8.0            # stopped before the 12s gap


def test_clamps_overlong_single_sentence():
    units = [_unit(0.0, 40.0)]                # one 40s sentence
    segs = _build_sentence_segments([_frame(5.0)], units, clip_s=12.0, max_segment_s=20.0)
    assert segs[0]["t_start"] == 0.0 and segs[0]["t_end"] == 20.0   # clamped to max_segment_s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_segment_selection_sentences.py -v`
Expected: FAIL — `cannot import name '_build_sentence_segments'`

- [ ] **Step 3: Implement the helper + constants**

In `src/services/segment_selection.py`:
1. Add the import near the top (with the other imports): `from src.services.transcript_units import build_sentence_units`
2. Add constants after the existing module constants (e.g. after `_MIN_SILENCE_GAP`):

```python
_TURN_GAP_S = 1.0    # transcript gap that likely marks a speaker turn; stop expanding
_SPEECH_TYPES = {"total", "teaser", "promo", "vtr", "nota", "highlights"}
```

3. Add these functions (next to `_build_per_frame_segments`):

```python
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
    exceeds max_segment_s."""
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
            if nxt["t_start"] - t_end > _TURN_GAP_S:        # speaker turn — stop
                break
            if (t_end - t_start) >= clip_s:                  # enough duration
                break
            if (nxt["t_end"] - t_start) > max_segment_s:     # would overflow cap
                break
            t_end = nxt["t_end"]
            j += 1
        if (t_end - t_start) > max_segment_s:                # degenerate long sentence
            t_end = t_start + max_segment_s
        segs.append({
            "t_start": t_start,
            "t_end": t_end,
            "max_score": f.get("puntuacion", 0),
            "hablante": f.get("hablante", "plano_sala"),
            "cargo": f.get("cargo_inferido") or "",
            "razon": f.get("razon_puntuacion", ""),
        })
    return segs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/services/test_segment_selection_sentences.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/segment_selection.py tests/unit/services/test_segment_selection_sentences.py
git commit -m "feat(segment-selection): _build_sentence_segments expands cuts to whole sentences"
```

---

### Task 3: Use sentence segments for speech types in `select_segments`

**Files:**
- Modify: `apps/api/src/services/segment_selection.py` (`select_segments`, the "Step B" build block)
- Test: `apps/api/tests/unit/services/test_segment_selection_sentences.py` (append)

- [ ] **Step 1: Write the failing end-to-end tests**

```python
# append to tests/unit/services/test_segment_selection_sentences.py
from src.services.segment_selection import select_segments


def _words_two_sentences():
    # two 4s sentences with a clean boundary; words drive build_sentence_units
    return [
        {"start": 0.0, "end": 0.5, "word": "Una"},
        {"start": 0.5, "end": 4.0, "word": "frase."},
        {"start": 4.2, "end": 4.7, "word": "Otra"},
        {"start": 4.7, "end": 8.0, "word": "frase."},
    ]


def test_nota_uses_sentence_aligned_cuts():
    frames = [{"timestamp_s": 1.0, "puntuacion": 8, "hablante": "Sánchez"}]
    out = select_segments(frames, target_duration=60.0, words=_words_two_sentences(),
                          tipo_pieza="nota")
    assert out, "expected at least one segment"
    # cut starts exactly at a sentence start (0.0), not at ts-1.5 = -0.5→0.0 window
    assert out[0]["t_start"] == 0.0


def test_broll_keeps_fixed_window():
    frames = [{"timestamp_s": 10.0, "puntuacion": 8, "hablante": "plano_sala"}]
    out = select_segments(frames, target_duration=60.0, words=_words_two_sentences(),
                          tipo_pieza="broll")
    # fixed window for b-roll: t_start = ts - 1.5 = 8.5 (not a sentence boundary)
    assert any(abs(s["t_start"] - 8.5) < 0.6 for s in out)


def test_speech_type_without_words_falls_back():
    frames = [{"timestamp_s": 10.0, "puntuacion": 8, "hablante": "Sánchez"}]
    out = select_segments(frames, target_duration=60.0, words=[], tipo_pieza="nota")
    # no transcript → fixed-window fallback, near ts-1.5
    assert out and any(abs(s["t_start"] - 8.5) < 0.6 for s in out)
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/unit/services/test_segment_selection_sentences.py -k "nota or broll or fallback" -v`
Expected: FAIL — `test_nota_uses_sentence_aligned_cuts` fails (current code builds a fixed window, so `t_start` ≈ 0.0 only by luck of clamping; the boundary assertion fails or b-roll/fallback differ). At minimum the nota assertion must fail before the change.

- [ ] **Step 3: Branch the builder in `select_segments`**

In `src/services/segment_selection.py`, find the existing "Step B — build candidate segments" block:

```python
    # Step B — build candidate segments
    if per_frame:
        segments = _build_per_frame_segments(candidates, clip_s)
    else:
        segments = _build_merged_segments(candidates, max_segment_s)
```

Replace it with:

```python
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
```

- [ ] **Step 4: Run the new tests and the full suite**

Run: `uv run pytest tests/unit/services/test_segment_selection_sentences.py -v`
Expected: PASS (all)

Run: `uv run pytest tests/unit -q`
Expected: PASS (whole suite green)

- [ ] **Step 5: Commit**

```bash
git add src/services/segment_selection.py tests/unit/services/test_segment_selection_sentences.py
git commit -m "feat(segment-selection): sentence-aligned cuts for speech piece types, fixed-window fallback"
```

---

## Manual verification (after Task 3)

With API + worker running on a branch that has this change + the candidate-moments feature, and the logging fix:
1. `POST /api/montaje/pieza-emision` (tipo `nota`/`vtr`) with a real source.
2. In the log, confirm `Segment selection [...]` still selects ~N segments, and spot-check that selected `t_start`/`t_end` land on sentence boundaries (start of a sentence, end of a sentence) rather than mid-word.

## Self-review notes

- **Spec coverage:** shared `build_sentence_units` (Task 1) ✓; `_build_sentence_segments` with whole-sentence expansion + `_TURN_GAP_S` + `max_segment_s` clamp (Task 2) ✓; speech-type branch + fixed-window fallback when no transcript / b-roll (Task 3) ✓; no changes to visual_analysis / wiring ✓; tests for expansion, turn-gap, clamp, b-roll, empty-words ✓.
- **Type consistency:** `build_sentence_units` used identically in candidate_moments and segment_selection; frame field `timestamp_s` and `puntuacion`/`hablante`/`cargo_inferido`/`razon_puntuacion` match `_build_per_frame_segments`; segment dict shape (`t_start`,`t_end`,`max_score`,`hablante`,`cargo`,`razon`) matches the existing builder so downstream selection is unaffected.
- **Deferred per spec:** diarization, other endpoints, transitions — no tasks (intentional).
