# Transcript-guided Frame Sampling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blind 12-frame uniform grid with content-driven frame sampling, so Gemini scores frames at transcript-relevant moments and cuts anchor to sentences.

**Architecture:** New pure-Python `candidate_moments` service turns the transcript (word timestamps + punctuation + silences) into ≤25 ranked timestamps. `visual_analysis.analyze_video_visually` gains an optional `candidate_moments` arg: when present it extracts one 512×288 frame per candidate (instead of the grid) and maps Gemini scores back by `frame_id`. `pieza-emision` wires the detector in. Grid behavior remains as fallback.

**Tech Stack:** Python 3.12, pytest, ffmpeg (subprocess), Gemini via existing adapter.

**Spec:** `docs/superpowers/specs/2026-05-30-transcript-guided-frame-sampling-design.md`

---

## File Structure

- Create: `src/services/candidate_moments.py` — detector (sentence units, scoring, ranking, grid floor)
- Create: `tests/unit/services/__init__.py` — package marker
- Create: `tests/unit/services/test_candidate_moments.py` — unit tests (no I/O)
- Modify: `src/services/visual_analysis.py` — `_extract_frames_at` helper, `candidate_moments` branch, `frame_id` alignment fix
- Modify: `src/routes/generar_pieza.py` (`pieza_emision`, ~line 565-575) — call detector, pass result

Run all commands from `apps/api/`.

---

### Task 1: Sentence-unit splitting

**Files:**
- Create: `apps/api/src/services/candidate_moments.py`
- Create: `apps/api/tests/unit/services/__init__.py`
- Test: `apps/api/tests/unit/services/test_candidate_moments.py`

- [ ] **Step 1: Create the test package marker**

```bash
touch tests/unit/services/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/services/test_candidate_moments.py
from src.services.candidate_moments import _build_units


def _w(start, end, word):
    return {"start": start, "end": end, "word": word}


def test_build_units_splits_on_sentence_punctuation():
    words = [_w(0.0, 0.4, "Hola"), _w(0.4, 0.9, "mundo."),
             _w(1.0, 1.4, "Otra"), _w(1.4, 1.9, "frase.")]
    units = _build_units(words)
    assert len(units) == 2
    assert units[0]["text"] == "Hola mundo."
    assert units[0]["t_start"] == 0.0 and units[0]["t_end"] == 0.9


def test_build_units_splits_on_silence_gap():
    # 1.0s gap between the two words → two units even without punctuation
    words = [_w(0.0, 0.4, "uno"), _w(1.4, 1.8, "dos")]
    units = _build_units(words)
    assert len(units) == 2
    assert units[0]["bounded_by_pause"] is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_candidate_moments.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.candidate_moments'`

- [ ] **Step 4: Implement `_build_units`**

```python
# src/services/candidate_moments.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/services/test_candidate_moments.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/services/candidate_moments.py tests/unit/services/__init__.py tests/unit/services/test_candidate_moments.py
git commit -m "feat(candidate-moments): split transcript words into sentence units"
```

---

### Task 2: Heuristic scoring of units

**Files:**
- Modify: `apps/api/src/services/candidate_moments.py`
- Test: `apps/api/tests/unit/services/test_candidate_moments.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/services/test_candidate_moments.py
from src.services.candidate_moments import _score_unit


def _unit(text, bounded_by_pause=False):
    return {"text": text, "bounded_by_pause": bounded_by_pause,
            "t_start": 0.0, "t_end": 1.0, "timestamp": 0.5}


def test_score_rewards_numbers():
    with_num = _score_unit(_unit("inversión de 2000 millones de euros este año"), [])
    without = _score_unit(_unit("vamos a hablar de varias cosas importantes hoy"), [])
    assert with_num > without


def test_score_rewards_tema_keywords():
    s = _score_unit(_unit("el plan ferroviario nacional avanza con fuerza"), ["ferroviario"])
    base = _score_unit(_unit("el plan nacional avanza con mucha fuerza"), ["ferroviario"])
    assert s > base
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_candidate_moments.py -k score -v`
Expected: FAIL — `cannot import name '_score_unit'`

- [ ] **Step 3: Implement `_score_unit`**

```python
# add to src/services/candidate_moments.py (after the constants)
_NUM_RE = re.compile(r"\d")
_MIN_WORDS = 4
_MAX_WORDS = 60


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/services/test_candidate_moments.py -k score -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/candidate_moments.py tests/unit/services/test_candidate_moments.py
git commit -m "feat(candidate-moments): heuristic unit scoring (numbers, keywords, length, pause)"
```

---

### Task 3: `find_candidate_moments` — rank, space, grid floor

**Files:**
- Modify: `apps/api/src/services/candidate_moments.py`
- Test: `apps/api/tests/unit/services/test_candidate_moments.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/services/test_candidate_moments.py
from src.services.candidate_moments import find_candidate_moments


def test_respects_budget_and_spacing():
    words = []
    t = 0.0
    for i in range(200):
        words.append(_w(t, t + 0.4, f"palabra{i}" + ("." if i % 5 == 4 else "")))
        t += 0.5
    out = find_candidate_moments(words, [], duration=t, tema="", budget=25)
    assert len(out) <= 25
    ts = [c["timestamp"] for c in out]
    assert ts == sorted(ts)                     # chronological
    assert all(b - a >= 4.0 for a, b in zip(ts, ts[1:]))   # min spacing


def test_grid_floor_fills_sparse_transcript():
    # Only one short utterance near t=2 in a 120s video → grid must add coverage
    words = [_w(2.0, 2.3, "hola."), _w(2.3, 2.6, "qué."), _w(2.6, 2.9, "tal.")]
    out = find_candidate_moments(words, [], duration=120.0, tema="", budget=10)
    assert any(c["source"] == "grid" for c in out)


def test_empty_input_returns_empty():
    assert find_candidate_moments([], [], duration=0.0) == []
    assert find_candidate_moments([], [], duration=100.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_candidate_moments.py -k "budget or grid or empty" -v`
Expected: FAIL — `cannot import name 'find_candidate_moments'`

- [ ] **Step 3: Implement `find_candidate_moments`**

```python
# add to src/services/candidate_moments.py
_MIN_SPACING_S = 4.0
_GRID_FRACTION = 0.2


def _far_enough(ts: float, chosen: list[dict]) -> bool:
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
        units = _build_units(words)
        if not units:
            return []

        tema_keywords = [t for t in re.findall(r"\w+", tema.lower()) if len(t) > 3]
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

        n_grid = budget - len(chosen)
        if n_grid > 0:
            step = duration / (n_grid + 1)
            for k in range(1, n_grid + 1):
                ts = round(step * k, 1)
                if _far_enough(ts, chosen):
                    chosen.append({
                        "timestamp": ts,
                        "t_start": max(0.0, ts - 2.0),
                        "t_end": ts + 2.0,
                        "text": "",
                        "score": 0.0,
                        "source": "grid",
                    })

        return sorted(chosen, key=lambda c: c["timestamp"])[:budget]
    except Exception as exc:  # never break the caller
        logger.warning("find_candidate_moments failed: %s", exc)
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/services/test_candidate_moments.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/candidate_moments.py tests/unit/services/test_candidate_moments.py
git commit -m "feat(candidate-moments): rank, enforce spacing, add grid coverage floor"
```

---

### Task 4: `visual_analysis` — extract frames at given timestamps

**Files:**
- Modify: `apps/api/src/services/visual_analysis.py`
- Test: `apps/api/tests/unit/services/test_visual_analysis_frames.py` (create)

- [ ] **Step 1: Write the failing test (signature + scale only — no ffmpeg run)**

```python
# tests/unit/services/test_visual_analysis_frames.py
import inspect
from src.services import visual_analysis


def test_extract_frames_at_exists_with_scale_default():
    fn = getattr(visual_analysis, "_extract_frames_at", None)
    assert fn is not None, "_extract_frames_at must exist"
    sig = inspect.signature(fn)
    assert "timestamps" in sig.parameters
    assert sig.parameters["scale"].default == "512:288"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_visual_analysis_frames.py -v`
Expected: FAIL — `_extract_frames_at must exist`

- [ ] **Step 3: Implement `_extract_frames_at`**

Add after `_extract_frames` in `src/services/visual_analysis.py`:

```python
async def _extract_frames_at(
    video_path: Path,
    ffmpeg: str,
    tmpdir: Path,
    timestamps: list[float],
    scale: str = "512:288",
) -> list[tuple[float, bytes]]:
    """Extract one JPEG per requested timestamp (content-driven sampling).

    Higher default resolution than the grid path so documents / expressions /
    on-screen text are legible to the scorer. A failed single extraction is
    skipped, not fatal.
    """
    frames: list[tuple[float, bytes]] = []
    for i, ts in enumerate(timestamps):
        out = tmpdir / f"c_{i:04d}.jpg"
        cmd = [
            ffmpeg, "-y", "-ss", f"{max(0.0, ts):.2f}", "-i", str(video_path),
            "-frames:v", "1", "-vf", f"scale={scale}", "-q:v", "4", str(out),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if out.exists():
            frames.append((round(ts, 1), out.read_bytes()))
    return frames
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/services/test_visual_analysis_frames.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/visual_analysis.py tests/unit/services/test_visual_analysis_frames.py
git commit -m "feat(visual): _extract_frames_at — content-driven frames at 512x288"
```

---

### Task 5: `analyze_video_visually` — candidate branch + frame_id alignment fix

**Files:**
- Modify: `apps/api/src/services/visual_analysis.py`

- [ ] **Step 1: Add the `candidate_moments` parameter and branch the frame extraction**

In `analyze_video_visually`, change the signature to add (after `max_frames`):

```python
    candidate_moments: list[dict] | None = None,
```

Replace the frame-extraction block (currently the `max_frames`/`interval`/`with tempfile.TemporaryDirectory()` section that calls `_extract_frames`) with:

```python
    with tempfile.TemporaryDirectory() as tmpdir:
        if candidate_moments:
            timestamps = [c["timestamp"] for c in candidate_moments]
            raw_frames = await _extract_frames_at(
                video_path, ffmpeg, Path(tmpdir), timestamps
            )
            cand_text = {round(c["timestamp"], 1): c.get("text", "") for c in candidate_moments}
        else:
            if max_frames is None:
                max_frames = min(max(4, int(duration / 5.0)), 12)
            interval = max(5.0, duration / max_frames)
            raw_frames = await _extract_frames(
                video_path, ffmpeg, Path(tmpdir), interval, max_frames
            )
            cand_text = {}
```

- [ ] **Step 2: Use candidate text for the transcript window when present**

In the loop that builds interleaved `content`, replace the `transcript_text = _get_transcript_window(...)` line with:

```python
        transcript_text = cand_text.get(ts) if cand_text else _get_transcript_window(words or [], ts)
        if transcript_text is None:
            transcript_text = _get_transcript_window(words or [], ts)
```

- [ ] **Step 3: Fix the score↔timestamp alignment (map by frame_id, not blind index)**

Replace the existing block:

```python
        # Stamp actual timestamps (model may have slightly different values)
        for i, frame in enumerate(scored):
            if i < len(raw_frames):
                frame["timestamp_s"] = raw_frames[i][0]
```

with:

```python
        # Map each scored frame back to the timestamp of the frame we actually
        # sent, by frame_id — robust to the model dropping/reordering frames.
        ts_by_id = {i: raw_frames[i][0] for i in range(len(raw_frames))}
        aligned: list[dict] = []
        for frame in scored:
            fid = frame.get("frame_id")
            if isinstance(fid, int) and fid in ts_by_id:
                frame["timestamp_s"] = ts_by_id[fid]
                aligned.append(frame)
            else:
                logger.warning("Dropping frame with unmappable frame_id=%r", fid)
        scored = aligned
        if len(scored) != len(raw_frames):
            logger.warning(
                "Frame count mismatch: sent %d, mapped %d", len(raw_frames), len(scored)
            )
```

- [ ] **Step 4: Run the full unit suite to confirm nothing breaks**

Run: `uv run pytest tests/unit -q`
Expected: PASS (existing tests + the new candidate_moments / frame tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/visual_analysis.py
git commit -m "feat(visual): candidate-driven frame branch + frame_id score alignment"
```

---

### Task 6: Wire the detector into `pieza-emision`

**Files:**
- Modify: `apps/api/src/routes/generar_pieza.py` (`pieza_emision`, ~line 562-575)

- [ ] **Step 1: Add the import**

At the top of `src/routes/generar_pieza.py`, with the other service imports:

```python
from src.services.candidate_moments import find_candidate_moments
```

- [ ] **Step 2: Build candidates and pass them to the scorer**

In `pieza_emision`, the current block transcribes then calls `analyze_video_visually` with `words=all_words_trans[i]`. Replace that visual-analysis `asyncio.gather` with one that first computes candidates per source:

```python
    # Content-driven sampling: pick the moments worth scoring from the transcript
    all_candidates = [
        find_candidate_moments(
            all_words_trans[i], all_segs_trans[i], dur,
            tema=body.titular.strip() or "", budget=25,
        )
        for i, (src, dur) in enumerate(zip(sources, source_durations))
    ]

    all_visual = await asyncio.gather(*[
        analyze_video_visually(
            src, ffmpeg, dur,
            words=all_words_trans[i],
            tipo_contenido=body.tipo_pieza,
            tema=body.titular.strip() or "",
            candidate_moments=all_candidates[i] or None,
        )
        for i, (src, dur) in enumerate(zip(sources, source_durations))
    ])
```

- [ ] **Step 3: Verify the route imports and the app boots**

Run: `uv run python -c "import src.routes.generar_pieza"`
Expected: no error (clean import)

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/routes/generar_pieza.py
git commit -m "feat(montaje): pieza-emision scores transcript-driven candidate frames"
```

---

## Manual verification (after Task 6)

With the API + worker running and a real source video uploaded:
1. `POST /api/montaje/pieza-emision` with that source.
2. Confirm in the API log: `Visual scoring: N frames` where N is closer to ~25 (not capped at 12) on a long video, and `Segment selection [...]` produces sentence-anchored cuts.
3. Spot-check that selected `t_start`/`t_end` land on sentence boundaries (not mid-word).

## Self-review notes

- **Spec coverage:** candidate detector (Tasks 1-3) ✓; 512×288 + candidate frames (Task 4-5) ✓; alignment bug fix (Task 5) ✓; pieza-emision wiring (Task 6) ✓; grid fallback preserved (Task 5 else-branch) ✓; tests ✓.
- **Deferred per spec:** audio emphasis, diarization, text-only endpoints — not in any task (intentional).
