# Multi-source colas (backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build cola/broll/total pieces correctly from 2-3 source videos by threading `fuente_index` end-to-end and making overlap pruning source-aware; nota/vtr with ≥2 sources fall back to per-frame selection.

**Architecture:** All logic changes live in the pure module `src/services/segment_selection.py`: (a) the three `_build_*` segment builders copy `fuente_index` from the scored frame; (b) a new `_overlaps(a, b)` helper makes overlap checks treat clips from different sources as independent; (c) `select_segments` gains an `n_fuentes` parameter that disables sentence alignment for multi-source speech types. One line in `src/routes/generar_pieza.py` passes `n_fuentes`. Everything downstream (`_scored_segments_to_plan`, `_deduplicate_segments`, `_snap_segment_boundaries`, the `fuentes[idx] → storage_key` mapping) is already source-aware and untouched.

**Tech Stack:** Python 3.12, pytest, FastAPI (route only at the call site). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-multi-source-colas-design.md`

**Setup for every test run:** from the repo root, `cd apps/api` first. The venv must be **Python 3.12** (uv may pick 3.14, which lacks a `greenlet` wheel). These are pure unit tests — no DB, Docker, or API keys needed.

---

## Task 1: Preserve `fuente_index` in the three segment builders

The builders rebuild the segment dict and currently drop `fuente_index`. This is the single point where it is lost. Also fix the `_build_sentence_segments` dedup key so two sources' identical local spans are not collapsed.

**Files:**
- Modify: `apps/api/src/services/segment_selection.py` (`_build_per_frame_segments` 69-85, `_build_sentence_segments` 98-147, `_build_merged_segments` 150-197)
- Test: `apps/api/tests/unit/services/test_segment_selection_multisource.py` (create)

- [ ] **Step 1: Write the failing tests** (create the file with shared helpers + builder tests)

Create `apps/api/tests/unit/services/test_segment_selection_multisource.py`:

```python
"""Multi-source (fuente_index) behaviour of segment selection.

These exercise the pure functions in segment_selection.py: that fuente_index
survives the segment builders, that overlap pruning is source-aware, and that
speech types fall back to per-frame selection when there is more than one source.
"""

from src.services.segment_selection import (
    _build_merged_segments,
    _build_per_frame_segments,
    _build_sentence_segments,
)
from src.services.transcript_units import build_sentence_units


def _frame(ts, score=5, fuente_index=0, hablante="plano_sala"):
    """A Gemini-scored frame as produced upstream (route adds fuente_index)."""
    return {
        "timestamp_s": ts,
        "puntuacion": score,
        "hablante": hablante,
        "cargo_inferido": "",
        "razon_puntuacion": "",
        "fuente_index": fuente_index,
    }


def _one_sentence_5_to_9():
    """One sentence, contiguous words 5.0→9.0s, no internal pause ≥0.35s
    (so _find_silences returns [] and Step C never snaps)."""
    return [
        {"word": "Una",       "start": 5.0, "end": 6.0},
        {"word": "frase",     "start": 6.0, "end": 7.0},
        {"word": "larga",     "start": 7.0, "end": 8.0},
        {"word": "completa.", "start": 8.0, "end": 9.0},
    ]


def test_per_frame_builder_preserves_fuente_index():
    frames = [_frame(10.0, fuente_index=0), _frame(20.0, fuente_index=1)]
    segs = _build_per_frame_segments(frames, clip_s=5.0)
    assert [s["fuente_index"] for s in segs] == [0, 1]


def test_per_frame_builder_defaults_fuente_index_to_zero():
    frame = {"timestamp_s": 10.0, "puntuacion": 5}  # no fuente_index key
    segs = _build_per_frame_segments([frame], clip_s=5.0)
    assert segs[0]["fuente_index"] == 0


def test_merged_builder_preserves_fuente_index():
    segs = _build_merged_segments([_frame(10.0, fuente_index=1)], max_segment_s=12.0)
    assert segs[0]["fuente_index"] == 1


def test_sentence_builder_preserves_fuente_index():
    units = build_sentence_units(_one_sentence_5_to_9())
    segs = _build_sentence_segments(
        [_frame(8.0, fuente_index=1, hablante="Juan")],
        units, clip_s=12.0, max_segment_s=12.0,
    )
    assert segs and segs[0]["fuente_index"] == 1


def test_sentence_builder_dedup_keeps_distinct_sources():
    # Two frames mapping to the same sentence span but different sources must
    # NOT collapse into one (dedup key includes fuente_index).
    units = build_sentence_units(_one_sentence_5_to_9())
    frames = [_frame(8.0, fuente_index=0), _frame(8.0, fuente_index=1)]
    segs = _build_sentence_segments(frames, units, clip_s=12.0, max_segment_s=12.0)
    assert sorted(s["fuente_index"] for s in segs) == [0, 1]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/unit/services/test_segment_selection_multisource.py -v`
Expected: FAIL — `test_per_frame_builder_preserves_fuente_index`, `test_merged_builder_preserves_fuente_index`, `test_sentence_builder_preserves_fuente_index` raise `KeyError: 'fuente_index'`; `test_sentence_builder_dedup_keeps_distinct_sources` collapses to one segment (assert fails).

- [ ] **Step 3: Add `fuente_index` to `_build_per_frame_segments`**

In `apps/api/src/services/segment_selection.py`, the dict appended in `_build_per_frame_segments` (lines 77-84). Replace:

```python
        segs.append({
            "t_start":   max(0.0, ts - 1.5),
            "t_end":     ts + clip_s - 1.5,
            "max_score": f.get("puntuacion", 0),
            "hablante":  f.get("hablante", "plano_sala"),
            "cargo":     f.get("cargo_inferido") or "",
            "razon":     f.get("razon_puntuacion", ""),
        })
```

with:

```python
        segs.append({
            "t_start":     max(0.0, ts - 1.5),
            "t_end":       ts + clip_s - 1.5,
            "max_score":   f.get("puntuacion", 0),
            "hablante":    f.get("hablante", "plano_sala"),
            "cargo":       f.get("cargo_inferido") or "",
            "razon":       f.get("razon_puntuacion", ""),
            "fuente_index": f.get("fuente_index", 0),
        })
```

- [ ] **Step 4: Add `fuente_index` to `_build_sentence_segments` and fix its dedup key**

In `_build_sentence_segments`, replace the appended dict (lines 132-139):

```python
        segs.append({
            "t_start": t_start,
            "t_end": t_end,
            "max_score": f.get("puntuacion", 0),
            "hablante": f.get("hablante", "plano_sala"),
            "cargo": f.get("cargo_inferido") or "",
            "razon": f.get("razon_puntuacion", ""),
        })
```

with:

```python
        segs.append({
            "t_start": t_start,
            "t_end": t_end,
            "max_score": f.get("puntuacion", 0),
            "hablante": f.get("hablante", "plano_sala"),
            "cargo": f.get("cargo_inferido") or "",
            "razon": f.get("razon_puntuacion", ""),
            "fuente_index": f.get("fuente_index", 0),
        })
```

Then replace the dedup block (lines 142-147):

```python
    unique: dict[tuple[float, float], dict] = {}
    for s in segs:
        key = (s["t_start"], s["t_end"])
        if key not in unique or s["max_score"] > unique[key]["max_score"]:
            unique[key] = s
    return sorted(unique.values(), key=lambda s: s["t_start"])
```

with:

```python
    unique: dict[tuple[int, float, float], dict] = {}
    for s in segs:
        key = (s["fuente_index"], s["t_start"], s["t_end"])
        if key not in unique or s["max_score"] > unique[key]["max_score"]:
            unique[key] = s
    return sorted(unique.values(), key=lambda s: s["t_start"])
```

- [ ] **Step 5: Add `fuente_index` to `_build_merged_segments`**

In `_build_merged_segments` there are two dict literals assigned to `current` (lines 163-170 and 180-187). Both have the same shape. Add `"fuente_index": frame.get("fuente_index", 0),` as the last key in **each**. After editing, both blocks read:

```python
            current = {
                "t_start":   max(0.0, ts - 3.0),
                "t_end":     ts + 8.0,
                "max_score": score,
                "hablante":  frame.get("hablante", "desconocido"),
                "cargo":     frame.get("cargo_inferido") or "",
                "razon":     frame.get("razon_puntuacion", ""),
                "fuente_index": frame.get("fuente_index", 0),
            }
```

(Apply to both occurrences. The first is inside `if current is None:`, the second inside the `else:` branch. The merge `elif` branch keeps the first frame's `fuente_index` — acceptable; this builder is only reached for unconfigured piece types.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/unit/services/test_segment_selection_multisource.py -v`
Expected: PASS (5 passed).

- [ ] **Step 7: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/services/segment_selection.py apps/api/tests/unit/services/test_segment_selection_multisource.py
git commit -m "feat(segment-selection): preserve fuente_index through segment builders"
```

---

## Task 2: Source-aware overlap pruning

Two clips from different source videos share a 0-based timeline but are independent footage. The overlap checks in `_select_recurso` and `_select_non_overlapping` must only treat clips as overlapping when they come from the same source.

**Files:**
- Modify: `apps/api/src/services/segment_selection.py` (add `_overlaps` near line 199; `_select_non_overlapping` 215-218; `_select_recurso._try_add` line 266)
- Test: `apps/api/tests/unit/services/test_segment_selection_multisource.py` (append)

- [ ] **Step 1: Write the failing tests** (append to the test file)

Add these imports to the existing `from src.services.segment_selection import (...)` block: `_overlaps`, `_select_non_overlapping`, `_select_recurso`. The block becomes:

```python
from src.services.segment_selection import (
    _build_merged_segments,
    _build_per_frame_segments,
    _build_sentence_segments,
    _overlaps,
    _select_non_overlapping,
    _select_recurso,
)
```

Append to the end of the file:

```python
def _seg(t_start, t_end, fuente_index=0, max_score=3, hablante="plano_sala"):
    """A built segment (post-builder shape)."""
    return {
        "t_start": t_start,
        "t_end": t_end,
        "fuente_index": fuente_index,
        "max_score": max_score,
        "hablante": hablante,
        "cargo": "",
        "razon": "",
    }


def test_overlaps_same_source_overlapping():
    assert _overlaps(_seg(10, 15, 0), _seg(12, 17, 0)) is True


def test_overlaps_different_source_not_overlapping():
    assert _overlaps(_seg(10, 15, 0), _seg(12, 17, 1)) is False


def test_overlaps_same_source_disjoint():
    assert _overlaps(_seg(10, 15, 0), _seg(20, 25, 0)) is False


def test_select_recurso_keeps_overlapping_clips_from_different_sources():
    segs = [_seg(10, 15, fuente_index=0), _seg(12, 17, fuente_index=1)]
    out = _select_recurso(segs, target_duration=60.0, max_segs=None)
    assert len(out) == 2
    assert {s["fuente_index"] for s in out} == {0, 1}


def test_select_recurso_drops_overlapping_clips_from_same_source():
    segs = [_seg(10, 15, fuente_index=0), _seg(12, 17, fuente_index=0)]
    out = _select_recurso(segs, target_duration=60.0, max_segs=None)
    assert len(out) == 1


def test_select_non_overlapping_keeps_different_sources():
    segs = [
        _seg(10, 22, fuente_index=0, max_score=9),
        _seg(12, 24, fuente_index=1, max_score=8),
    ]
    out = _select_non_overlapping(segs, target_duration=60.0, max_segs=None)
    assert len(out) == 2


def test_select_non_overlapping_drops_same_source_overlap():
    segs = [
        _seg(10, 22, fuente_index=0, max_score=9),
        _seg(12, 24, fuente_index=0, max_score=8),
    ]
    out = _select_non_overlapping(segs, target_duration=60.0, max_segs=None)
    assert len(out) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/unit/services/test_segment_selection_multisource.py -v`
Expected: FAIL — the import of `_overlaps` raises `ImportError` (function not defined), so the whole module errors at collection.

- [ ] **Step 3: Add the `_overlaps` helper**

In `apps/api/src/services/segment_selection.py`, insert this function immediately **before** `def _select_non_overlapping(` (currently line 200), after the blank line that follows `_build_merged_segments`:

```python
def _overlaps(a: dict, b: dict) -> bool:
    """Two segments overlap only if they come from the same source AND their
    time ranges intersect. Clips from different sources share a 0-based timeline
    but are independent footage, so they never block each other."""
    return (
        a.get("fuente_index", 0) == b.get("fuente_index", 0)
        and a["t_start"] < b["t_end"]
        and a["t_end"] > b["t_start"]
    )
```

- [ ] **Step 4: Use `_overlaps` in `_select_non_overlapping`**

Replace the overlap check (lines 215-218):

```python
        overlaps = any(
            seg["t_start"] < sel["t_end"] and seg["t_end"] > sel["t_start"]
            for sel in selected
        )
```

with:

```python
        overlaps = any(_overlaps(seg, sel) for sel in selected)
```

- [ ] **Step 5: Use `_overlaps` in `_select_recurso._try_add`**

Replace the overlap check inside `_try_add` (line 266):

```python
        if any(seg["t_start"] < s["t_end"] and seg["t_end"] > s["t_start"] for s in selected):
            return
```

with:

```python
        if any(_overlaps(seg, s) for s in selected):
            return
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/unit/services/test_segment_selection_multisource.py -v`
Expected: PASS (12 passed — 5 from Task 1 + 7 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/services/segment_selection.py apps/api/tests/unit/services/test_segment_selection_multisource.py
git commit -m "feat(segment-selection): source-aware overlap pruning for multi-source clips"
```

---

## Task 3: `n_fuentes` guard for speech types + call-site wiring

Speech types (nota/vtr) sentence-align cuts using the flattened transcript, which is non-monotonic across sources. With ≥2 sources, disable sentence alignment so they fall back to the per-frame builder (correct source routing, visual cuts). Single-source keeps sentence alignment unchanged.

**Files:**
- Modify: `apps/api/src/services/segment_selection.py` (`select_segments` signature 330-337; Step B `units` 370)
- Modify: `apps/api/src/routes/generar_pieza.py` (call site 657-662)
- Test: `apps/api/tests/unit/services/test_segment_selection_multisource.py` (append)

- [ ] **Step 1: Write the failing tests** (append to the test file)

Add `select_segments` to the import block:

```python
from src.services.segment_selection import (
    _build_merged_segments,
    _build_per_frame_segments,
    _build_sentence_segments,
    _overlaps,
    _select_non_overlapping,
    _select_recurso,
    select_segments,
)
```

Append:

```python
def _speech_frame(ts, fuente_index=0, score=8):
    return {
        "timestamp_s": ts,
        "puntuacion": score,
        "hablante": "Juan",
        "cargo_inferido": "",
        "razon_puntuacion": "",
        "fuente_index": fuente_index,
    }


def test_speech_single_source_uses_sentence_alignment():
    # nota + 1 source → cut starts at the sentence start (5.0), not the
    # per-frame window start (ts - 1.5 = 6.5).
    out = select_segments(
        [_speech_frame(8.0, fuente_index=0)],
        target_duration=60.0,
        words=_one_sentence_5_to_9(),
        tipo_pieza="nota",
        n_fuentes=1,
    )
    assert len(out) == 1
    assert out[0]["t_start"] == 5.0


def test_speech_multisource_falls_back_to_per_frame():
    # nota + 2 sources → no sentence alignment; cut starts at the per-frame
    # window start (ts - 1.5 = 6.5), and fuente_index is preserved.
    out = select_segments(
        [_speech_frame(8.0, fuente_index=0)],
        target_duration=60.0,
        words=_one_sentence_5_to_9(),
        tipo_pieza="nota",
        n_fuentes=2,
    )
    assert len(out) == 1
    assert out[0]["t_start"] == 6.5
    assert out[0]["fuente_index"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/unit/services/test_segment_selection_multisource.py -v`
Expected: FAIL — `select_segments()` does not accept `n_fuentes`, raising `TypeError: select_segments() got an unexpected keyword argument 'n_fuentes'`.

- [ ] **Step 3: Add the `n_fuentes` parameter to `select_segments`**

Replace the signature (lines 330-337):

```python
def select_segments(
    scored_frames: list[dict],
    target_duration: float,
    words: list[dict] | None = None,
    min_segment_s: float = 5.0,
    max_segment_s: float = 12.0,
    tipo_pieza: str = "vtr",
) -> list[dict]:
```

with:

```python
def select_segments(
    scored_frames: list[dict],
    target_duration: float,
    words: list[dict] | None = None,
    min_segment_s: float = 5.0,
    max_segment_s: float = 12.0,
    tipo_pieza: str = "vtr",
    n_fuentes: int = 1,
) -> list[dict]:
```

- [ ] **Step 4: Disable sentence alignment for multi-source speech (Step B)**

Replace the `units` line in Step B (line 370):

```python
    units = build_sentence_units(words or []) if tipo_pieza in _SPEECH_TYPES else []
```

with:

```python
    # Multi-source speech: the flattened transcript timeline is non-monotonic
    # across sources, so sentence alignment is unreliable — fall back to the
    # per-frame builder (per_frame=True for all speech types). Single-source
    # speech keeps sentence alignment.
    units = (
        build_sentence_units(words or [])
        if tipo_pieza in _SPEECH_TYPES and n_fuentes < 2
        else []
    )
```

- [ ] **Step 5: Run the segment-selection tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/unit/services/test_segment_selection_multisource.py -v`
Expected: PASS (14 passed).

- [ ] **Step 6: Wire `n_fuentes` at the route call site**

In `apps/api/src/routes/generar_pieza.py`, replace the `select_segments` call (lines 657-662):

```python
        score_selected = select_segments(
            scored_frames_flat,
            target_duration=float(duracion_efectiva),
            words=words_flat,
            tipo_pieza=body.tipo_pieza,
        )
```

with:

```python
        score_selected = select_segments(
            scored_frames_flat,
            target_duration=float(duracion_efectiva),
            words=words_flat,
            tipo_pieza=body.tipo_pieza,
            n_fuentes=len(body.fuentes),
        )
```

- [ ] **Step 7: Verify the route module imports cleanly (no syntax/type slip)**

Run: `cd apps/api && uv run python -c "import src.routes.generar_pieza"`
Expected: no output, exit code 0.

- [ ] **Step 8: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/services/segment_selection.py apps/api/src/routes/generar_pieza.py apps/api/tests/unit/services/test_segment_selection_multisource.py
git commit -m "feat(segment-selection): n_fuentes guard routes multi-source speech to per-frame"
```

---

## Task 4: End-to-end `fuente_index` preservation through `select_segments`

A regression test that exercises the whole `select_segments` path for a multi-source cola: mixed sources survive (no cross-source false overlaps) and carry the correct `fuente_index` on output — the property the route relies on to map each clip to `fuentes[idx]`.

**Files:**
- Test: `apps/api/tests/unit/services/test_segment_selection_multisource.py` (append)

- [ ] **Step 1: Write the test** (append to the test file)

```python
def test_select_segments_cola_preserves_mixed_fuente_index():
    # cola → per-frame + _select_recurso. Clips from sources 0 and 1 with
    # overlapping local times must all survive and keep their fuente_index.
    frames = [
        _frame(10.0, score=3, fuente_index=0),
        _frame(12.0, score=3, fuente_index=1),  # overlaps src 0 in local time
        _frame(40.0, score=3, fuente_index=0),
    ]
    out = select_segments(
        frames, target_duration=60.0, words=[], tipo_pieza="cola", n_fuentes=2,
    )
    assert len(out) == 3
    assert {s["fuente_index"] for s in out} == {0, 1}
    assert all("fuente_index" in s for s in out)
```

- [ ] **Step 2: Run the test to verify it passes**

This test passes on the code from Tasks 1-3 (it is a regression guard, not a new behaviour). Run:
`cd apps/api && uv run pytest tests/unit/services/test_segment_selection_multisource.py::test_select_segments_cola_preserves_mixed_fuente_index -v`
Expected: PASS. (If it fails, a prior task's change regressed — fix there, do not weaken this test.)

- [ ] **Step 3: Run the full unit suite — no regressions**

Run: `cd apps/api && uv run pytest tests/unit -v`
Expected: PASS — all previously-green tests (62) plus the 15 new ones in this file. Pay attention to `test_segment_selection_recurso.py` and `test_segment_selection_sentences.py`: they call `select_segments` without `n_fuentes`, which must still default to single-source behaviour.

- [ ] **Step 4: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/tests/unit/services/test_segment_selection_multisource.py
git commit -m "test(segment-selection): end-to-end multi-source fuente_index preservation"
```

---

## After all tasks

- Update `HANDOFF.md` (current state: multi-source colas backend done, tests green; deferred items unchanged) in the final commit or a follow-up commit, per the repo's handoff-on-commit rule.
- Use `superpowers:finishing-a-development-branch` to verify tests and choose merge/PR. The branch is `feat/multi-source-colas`, base `develop`. PRs are opened via the GitHub URL (no `gh` CLI); commit email for this repo is `luiscbravo94@gmail.com`.
- The frontend already sends `fuentes: [...]`, so no coordination commit is needed — this backend change makes the existing multi-source request route clips correctly.
