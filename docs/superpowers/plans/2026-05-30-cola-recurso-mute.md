# Cola = Recurso + Muted Audio — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cola` (and `broll`) pieces select recurso footage (non-declaration frames) and carry no audio, so the piece is real b-roll for the presenter to narrate over — not a muted nota.

**Architecture:** (1) `segment_selection` gets a recurso-first selector for `{cola, broll}` that prefers non-speaker / low-declaration frames and falls back to least-declaration. (2) `montaje` gains a `mute_clips` flag that drops the audio stream (`-an`) when cutting clips; the graphics overlay already maps audio optionally (`-map 0:a?`), so a muted input stays muted end to end. (3) `pieza_emision` passes `mute_clips=True` for cola/broll.

**Tech Stack:** Python 3.12, pytest, ffmpeg (subprocess).

**Spec:** `docs/superpowers/specs/2026-05-30-cola-recurso-mute-design.md`

---

## File Structure

- Modify: `src/services/segment_selection.py` — `_select_recurso`, constants, Step D branch
- Modify: `src/routes/montaje.py` — extract `_normalize_cmd`, add `mute` to `_normalizar_clip`, add `mute_clips` to `ensamblar`
- Modify: `src/routes/generar_pieza.py` — pass `mute_clips` in `pieza_emision`
- Test: `tests/unit/services/test_segment_selection_recurso.py` (create)
- Test: `tests/unit/routes/test_montaje_mute.py` (create; needs `tests/unit/routes/__init__.py`)

Run all commands from `apps/api/`.

---

### Task 1: Recurso-first selection for cola/broll

**Files:**
- Modify: `apps/api/src/services/segment_selection.py`
- Test: `apps/api/tests/unit/services/test_segment_selection_recurso.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_segment_selection_recurso.py
from src.services.segment_selection import _select_recurso


def _seg(t_start, t_end, score, hablante="plano_sala"):
    return {"t_start": t_start, "t_end": t_end, "max_score": score,
            "hablante": hablante, "cargo": "", "razon": ""}


def test_prefers_recurso_over_speaker():
    segs = [
        _seg(0.0, 8.0, 9, "Pedro Sánchez"),   # declaration — should be avoided
        _seg(10.0, 18.0, 2, "plano_sala"),    # recurso
        _seg(20.0, 28.0, 3, "desconocido"),   # recurso
    ]
    out = _select_recurso(segs, target_duration=30.0, max_segs=None)
    hablantes = {s["hablante"] for s in out}
    assert "Pedro Sánchez" not in hablantes      # speaker declaration excluded
    assert len(out) == 2                          # the two recurso clips


def test_falls_back_to_lowest_score_when_no_recurso():
    # all high-score named speaker → no recurso; fallback picks the LOWEST score
    segs = [
        _seg(0.0, 8.0, 9, "Pedro Sánchez"),
        _seg(10.0, 18.0, 7, "Pedro Sánchez"),
        _seg(20.0, 28.0, 8, "Pedro Sánchez"),
    ]
    out = _select_recurso(segs, target_duration=10.0, max_segs=None)
    assert len(out) == 1
    assert out[0]["max_score"] == 7               # least-declaration, not the 9


def test_no_overlap_and_chronological():
    segs = [_seg(0.0, 8.0, 2), _seg(4.0, 12.0, 2), _seg(20.0, 28.0, 2)]
    out = _select_recurso(segs, target_duration=60.0, max_segs=None)
    ts = [s["t_start"] for s in out]
    assert ts == sorted(ts)
    # overlapping 0-8 and 4-12 cannot both be selected
    assert not (0.0 in ts and 4.0 in ts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_segment_selection_recurso.py -v`
Expected: FAIL — `cannot import name '_select_recurso'`

- [ ] **Step 3: Implement constants + `_select_recurso`**

In `src/services/segment_selection.py`, add constants after `_SPEECH_TYPES`:

```python
_RECURSO_TYPES = {"cola", "broll"}   # b-roll recurso, no narration → recurso frames, muted
_RECURSO_MAX_SCORE = 4               # Gemini band 1-4 = listening / wide / no active speech
```

Add this function near `_select_non_overlapping`:

```python
def _select_recurso(
    segments: list[dict],
    target_duration: float,
    max_segs: int | None,
) -> list[dict]:
    """Select recurso (non-declaration) clips for cola/broll, chronologically,
    skipping overlaps. Prefers non-speaker / low-score frames; if recurso does
    not fill the target, fills with the LEAST-declaration (lowest-score) rest —
    never the top speaker frames."""
    def _is_recurso(s: dict) -> bool:
        return (
            s.get("hablante", "") in ("plano_sala", "desconocido", "")
            or s.get("max_score", 0) <= _RECURSO_MAX_SCORE
        )

    recurso = sorted((s for s in segments if _is_recurso(s)), key=lambda s: s["t_start"])
    fallback = sorted((s for s in segments if not _is_recurso(s)), key=lambda s: s["max_score"])

    selected: list[dict] = []
    total = 0.0

    def _try_add(seg: dict) -> None:
        nonlocal total
        if max_segs is not None and len(selected) >= max_segs:
            return
        if any(seg["t_start"] < s["t_end"] and seg["t_end"] > s["t_start"] for s in selected):
            return  # overlaps an already-selected clip
        dur = seg["t_end"] - seg["t_start"]
        if total + dur > target_duration * 1.05:
            return
        selected.append(seg)
        total += dur

    for seg in recurso:
        _try_add(seg)
    for seg in fallback:               # only used if recurso left the piece short
        if total >= target_duration * 0.95:
            break
        _try_add(seg)

    return sorted(selected, key=lambda s: s["t_start"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/services/test_segment_selection_recurso.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/segment_selection.py tests/unit/services/test_segment_selection_recurso.py
git commit -m "feat(segment-selection): _select_recurso for cola/broll (recurso-first, fallback lowest)"
```

---

### Task 2: Route cola/broll through `_select_recurso` in `select_segments`

**Files:**
- Modify: `apps/api/src/services/segment_selection.py` (`select_segments`, Step D)
- Test: `apps/api/tests/unit/services/test_segment_selection_recurso.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/services/test_segment_selection_recurso.py
from src.services.segment_selection import select_segments


def _frame(ts, score, hablante):
    return {"timestamp_s": ts, "puntuacion": score, "hablante": hablante}


def test_cola_routes_to_recurso_selection():
    # one high-score speaker frame + recurso frames; cola must NOT pick the speaker
    frames = [
        _frame(2.0, 9, "Pedro Sánchez"),
        _frame(20.0, 2, "plano_sala"),
        _frame(40.0, 3, "plano_sala"),
    ]
    out = select_segments(frames, target_duration=60.0, words=[], tipo_pieza="cola")
    assert out
    assert all(s["hablante"] != "Pedro Sánchez" for s in out)


def test_nota_still_picks_high_score_speaker():
    frames = [
        _frame(2.0, 9, "Pedro Sánchez"),
        _frame(20.0, 2, "plano_sala"),
    ]
    out = select_segments(frames, target_duration=60.0, words=[], tipo_pieza="nota")
    # nota keeps declaration-first behaviour → the score-9 speaker is selected
    assert any(s["hablante"] == "Pedro Sánchez" for s in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_segment_selection_recurso.py -k "cola_routes or nota_still" -v`
Expected: FAIL — `test_cola_routes_to_recurso_selection` fails (cola currently sorts by score desc → picks the speaker).

- [ ] **Step 3: Add the Step D branch**

In `select_segments`, find the Step D selection block. It currently looks like:

```python
    # Step D — type-specific selection
    if tipo_pieza == "total":
        result = _select_for_total(segments, target_duration)
    elif tipo_pieza == "highlights":
        result = sorted(segments, key=lambda s: s["t_start"])
    elif tipo_pieza in ("vtr", "nota"):
        result = _select_non_overlapping(segments, target_duration, max_segs)
    else:
```

Insert a new branch BEFORE the final `else`:

```python
    elif tipo_pieza in _RECURSO_TYPES:
        result = _select_recurso(segments, target_duration, max_segs)
```

(So the order is: total → highlights → vtr/nota → **cola/broll** → else.)

- [ ] **Step 4: Run the new tests and the full suite**

Run: `uv run pytest tests/unit/services/test_segment_selection_recurso.py -v`
Expected: PASS (all)

Run: `uv run pytest tests/unit -q`
Expected: PASS (whole suite green)

- [ ] **Step 5: Commit**

```bash
git add src/services/segment_selection.py tests/unit/services/test_segment_selection_recurso.py
git commit -m "feat(segment-selection): route cola/broll through recurso selection"
```

---

### Task 3: Muted clips in montaje

**Files:**
- Modify: `apps/api/src/routes/montaje.py`
- Test: `apps/api/tests/unit/routes/test_montaje_mute.py` (create)

- [ ] **Step 1: Create the test package marker + write the failing test**

```bash
mkdir -p tests/unit/routes && touch tests/unit/routes/__init__.py
```

```python
# tests/unit/routes/test_montaje_mute.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/routes/test_montaje_mute.py -v`
Expected: FAIL — `cannot import name '_normalize_cmd'`

- [ ] **Step 3: Extract `_normalize_cmd` and add `mute`**

In `src/routes/montaje.py`, replace the body of `_normalizar_clip` (the function that builds and runs the cut/re-encode command) so the command construction lives in a pure, testable helper. Add this module-level function (above `_normalizar_clip`):

```python
def _normalize_cmd(
    ffmpeg: str, source: Path, t_start: float, t_end: float | None,
    out: Path, mute: bool = False,
) -> list[str]:
    """Build the ffmpeg cut/re-encode command. When mute, drop the audio stream."""
    vf = (
        f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
        f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )
    cmd = [ffmpeg, "-y", "-ss", str(t_start)]
    if t_end is not None:
        cmd += ["-t", str(round(t_end - t_start, 3))]
    cmd += [
        "-i", str(source),
        "-vf", vf,
        "-r", "25", "-vsync", "cfr", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
    ]
    if mute:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd
```

Then change `_normalizar_clip` to accept `mute` and use the helper:

```python
async def _normalizar_clip(
    source: Path, t_start: float, t_end: float | None, out: Path, mute: bool = False,
) -> None:
    """Cut and re-encode to 1280x720 h264/aac so all clips are concat-compatible.
    When mute, the clip is rendered with no audio stream (-an)."""
    cmd = _normalize_cmd(_ffmpeg(), source, t_start, t_end, out, mute=mute)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Clip normalisation failed ({source.name}): {stderr.decode()[-300:]}")
```

- [ ] **Step 4: Add `mute_clips` to `ensamblar`**

In `ensamblar`, change the signature to add `mute_clips: bool = False` (alongside the other kwargs), and in the clip loop pass it through:

```python
            await _normalizar_clip(src, seg.tiempo_inicio, seg.tiempo_fin, clip, mute=mute_clips)
```

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/unit/routes/test_montaje_mute.py -v` → PASS
Run: `uv run python -c "import src.routes.montaje"` → clean
Run: `uv run pytest tests/unit -q` → green

- [ ] **Step 6: Commit**

```bash
git add src/routes/montaje.py tests/unit/routes/__init__.py tests/unit/routes/test_montaje_mute.py
git commit -m "feat(montaje): mute_clips flag drops clip audio (-an) for recurso pieces"
```

---

### Task 4: Wire `mute_clips` into `pieza_emision`

**Files:**
- Modify: `apps/api/src/routes/generar_pieza.py` (`pieza_emision`, the base-video `ensamblar` call ~line 736)

- [ ] **Step 1: Pass `mute_clips` for cola/broll**

In `pieza_emision`, find the base-video assembly call:

```python
        video_key, duration, material_en_loop = await ensamblar(
            base_segs,
            duracion_objetivo=duracion_efectiva,
            normalize_audio=False,   # normalization runs as the final paso, after grafismos+voiceover
        )
```

Add the `mute_clips` argument:

```python
        video_key, duration, material_en_loop = await ensamblar(
            base_segs,
            duracion_objetivo=duracion_efectiva,
            normalize_audio=False,   # normalization runs as the final paso, after grafismos+voiceover
            mute_clips=body.tipo_pieza in {"cola", "broll"},
        )
```

No change needed in `_apply_grafismos`: it maps audio optionally (`-map "0:a?"`), so a muted (no-audio) base video produces a muted graphics output. The final loudness step already tolerates a missing audio track.

- [ ] **Step 2: Verify import + full suite**

Run: `uv run python -c "import src.routes.generar_pieza"` → clean
Run: `uv run pytest tests/unit -q` → green

- [ ] **Step 3: Commit**

```bash
git add src/routes/generar_pieza.py
git commit -m "feat(montaje): pieza-emision mutes clip audio for cola/broll pieces"
```

---

## Manual verification (after Task 4)

With API + worker running and a real source video, via `/redactor` or curl `POST /api/montaje/pieza-emision` with `tipo_pieza="cola"`:
1. In the log, confirm `Segment selection [cola]` now selects recurso (the chosen `hablante` should be `plano_sala`/`desconocido`, not the named speaker) — add a temporary debug log of the selected segments if needed.
2. `ffprobe -i <output cola mp4> -show_streams` → there must be **no audio stream** (or run `ffprobe ... -select_streams a` → empty).
3. Compare against a `nota` of the same source: the nota keeps the speaker + audio; the cola shows recurso + silence.

## Self-review notes

- **Spec coverage:** recurso-first selection w/ fallback (Task 1) ✓; route cola/broll (Task 2) ✓; mute via `-an` (Task 3) ✓; pieza_emision wiring (Task 4) ✓; grafismos preserves no-audio (verified, Task 4 note) ✓; nota/vtr unchanged (Task 2 test) ✓; tests for recurso + mute ✓.
- **Type consistency:** `_select_recurso(segments, target_duration, max_segs)` returns the same segment dict shape (`t_start,t_end,max_score,hablante,cargo,razon`) the other selectors return; `_normalize_cmd`/`_normalizar_clip`/`ensamblar` thread `mute`/`mute_clips` consistently; frame fields `puntuacion`/`hablante` match `_build_per_frame_segments`.
- **Deferred per spec:** `off` pieces, archive b-roll, the text-only `generar-pieza` endpoint — no tasks (intentional).
