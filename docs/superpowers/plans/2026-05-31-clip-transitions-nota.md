# Crossfade transitions between clips (nota) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `nota`, join clips with a ~0.4s crossfade (xfade video + acrossfade audio) instead of a hard cut; every other piece type keeps the current hard-cut concat.

**Architecture:** Add a pure `_build_xfade_filter(durations, transition_s, mute)` that builds the `filter_complex` for a chained xfade/acrossfade with cumulative offsets (and per-input fps/format/sar/timebase normalization). `ensamblar` gains `transition_s`: when >0 with ≥2 clips it probes each normalised clip's duration and runs `_concat_xfade` (one re-encode) instead of the stream-copy `_concat`. A `_transition_for(tipo_pieza)` helper returns 0.4 for `nota` else 0.0, used at the `pieza_emision` call site.

**Tech Stack:** Python 3.12, FFmpeg (`xfade`/`acrossfade`), pytest.

**Spec:** `docs/superpowers/specs/2026-05-31-clip-transitions-nota-design.md`

**Setup for every test run:** `cd apps/api` first; venv is **Python 3.12**. The filter/decision tests are pure (no FFmpeg execution). `montaje.py` already has `_ffmpeg()`, `_concat`, `_normalizar_clip`, `_get_duration`, and `ensamblar`.

---

## Task 1: Pure `_build_xfade_filter` helper

**Files:**
- Modify: `apps/api/src/routes/montaje.py` (add after `_concat`, ~line 140)
- Test: `apps/api/tests/unit/routes/test_montaje_transitions.py` (create)

- [ ] **Step 1: Write the failing tests** — create `apps/api/tests/unit/routes/test_montaje_transitions.py`:

```python
"""Crossfade transition filter builder + per-type transition decision."""

from src.routes.montaje import _build_xfade_filter


def test_two_clips_video_xfade_and_audio_acrossfade():
    f = _build_xfade_filter([5.0, 6.0], 0.4)
    # per-input normalization so xfade never errors on format/fps/sar/timebase
    assert "[0:v]fps=25,format=yuv420p,setsar=1,settb=AVTB[s0]" in f
    assert "[1:v]fps=25,format=yuv420p,setsar=1,settb=AVTB[s1]" in f
    # single video xfade at offset d0 - T, output [vout]
    assert "[s0][s1]xfade=transition=fade:duration=0.40:offset=4.60[vout]" in f
    # single audio acrossfade, output [aout]
    assert "[0:a][1:a]acrossfade=d=0.40[aout]" in f


def test_three_clips_cumulative_offsets():
    f = _build_xfade_filter([5.0, 6.0, 7.0], 0.4)
    assert "offset=4.60" in f                      # running(5.0) - 0.4
    assert "offset=10.20" in f                     # running(5+6-0.4=10.6) - 0.4
    assert f.count("xfade=") == 2
    assert f.count("acrossfade=") == 2
    assert "[vout]" in f and "[aout]" in f


def test_fewer_than_two_clips_returns_empty():
    assert _build_xfade_filter([5.0], 0.4) == ""
    assert _build_xfade_filter([], 0.4) == ""
    assert _build_xfade_filter([5.0, 6.0], 0.0) == ""


def test_transition_clamped_to_half_shortest_clip():
    # shortest clip 0.5s → T clamps to 0.25 so the xfade offset never goes negative
    f = _build_xfade_filter([0.5, 0.5], 0.4)
    assert "duration=0.25" in f
    assert "offset=0.25" in f                      # 0.5 - 0.25


def test_mute_emits_video_only_no_acrossfade():
    f = _build_xfade_filter([5.0, 6.0], 0.4, mute=True)
    assert "xfade=" in f and "[vout]" in f
    assert "acrossfade=" not in f
    assert "[aout]" not in f
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/routes/test_montaje_transitions.py -v`
  Expected: FAIL — `_build_xfade_filter` not importable.

- [ ] **Step 3: Implement the helper** — in `montaje.py`, after `_concat` (~line 140) add:

```python
def _build_xfade_filter(durations: list[float], transition_s: float, mute: bool = False) -> str:
    """filter_complex to join N normalised clips with a crossfade: chained `xfade`
    on video (cumulative offsets) + chained `acrossfade` on audio (same duration, so
    audio and video shrink in lockstep). Each input is first normalised
    (fps/format/sar/timebase) so xfade never errors on edge-case source metadata.
    Returns "" when there is nothing to crossfade (caller falls back to plain concat).
    """
    n = len(durations)
    if n < 2 or transition_s <= 0:
        return ""
    t = min(transition_s, min(durations) / 2)   # never exceed half the shortest clip

    parts: list[str] = []
    for i in range(n):
        parts.append(f"[{i}:v]fps=25,format=yuv420p,setsar=1,settb=AVTB[s{i}]")

    prev, running = "s0", durations[0]
    for j in range(1, n):
        out = "vout" if j == n - 1 else f"vx{j}"
        offset = running - t
        parts.append(
            f"[{prev}][s{j}]xfade=transition=fade:duration={t:.2f}:offset={offset:.2f}[{out}]"
        )
        prev, running = out, running + durations[j] - t

    if not mute:
        aprev = "0:a"
        for j in range(1, n):
            out = "aout" if j == n - 1 else f"ax{j}"
            parts.append(f"[{aprev}][{j}:a]acrossfade=d={t:.2f}[{out}]")
            aprev = out

    return ";".join(parts)
```

- [ ] **Step 4: Run to verify it passes** — `cd apps/api && uv run pytest tests/unit/routes/test_montaje_transitions.py -v`
  Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/montaje.py apps/api/tests/unit/routes/test_montaje_transitions.py
git commit -m "feat(montaje): _build_xfade_filter (crossfade video + acrossfade audio chain)"
```

---

## Task 2: Wire crossfade into `ensamblar` + the nota call site

**Files:**
- Modify: `apps/api/src/routes/montaje.py` (add `_concat_xfade` + `_transition_for` + `_TRANSITION_S`; `ensamblar` signature + step 2 branch)
- Modify: `apps/api/src/routes/generar_pieza.py` (the `ensamblar(...)` call in `pieza_emision`, ~line 741)
- Test: `apps/api/tests/unit/routes/test_montaje_transitions.py` (append)

- [ ] **Step 1: Write the failing test** — append:

```python
from src.routes.montaje import _transition_for, _TRANSITION_S


def test_transition_for_only_nota():
    assert _transition_for("nota") == _TRANSITION_S
    assert _TRANSITION_S > 0
    for tipo in ("cola", "vtr", "total", "broll", "off", "highlights", "promo", "teaser"):
        assert _transition_for(tipo) == 0.0
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/routes/test_montaje_transitions.py -k transition_for -v`
  Expected: FAIL — `_transition_for` / `_TRANSITION_S` not importable.

- [ ] **Step 3: Add the constant + decision helper + the xfade runner** — in `montaje.py`, add near the top constants:

```python
_TRANSITION_S = 0.4   # crossfade duration between clips (seconds), nota only for now


def _transition_for(tipo_pieza: str) -> float:
    """Crossfade duration to use for a piece type. Only `nota` gets transitions for now."""
    return _TRANSITION_S if tipo_pieza == "nota" else 0.0
```

Then add the runner next to `_concat` (it reuses `_build_xfade_filter`):

```python
async def _concat_xfade(
    clip_paths: list[Path], durations: list[float], transition_s: float,
    out: Path, mute: bool = False,
) -> None:
    """Join clips with a crossfade (xfade video + acrossfade audio) in one re-encode."""
    filter_complex = _build_xfade_filter(durations, transition_s, mute=mute)
    inputs: list[str] = []
    for p in clip_paths:
        inputs += ["-i", str(p)]
    maps = ["-map", "[vout]"]
    if not mute:
        maps += ["-map", "[aout]"]
    cmd = (
        [_ffmpeg(), "-y", *inputs, "-filter_complex", filter_complex, *maps,
         "-r", "25", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-preset", "fast", "-crf", "23"]
    )
    if not mute:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", str(out)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Crossfade concat failed: {stderr.decode()[-300:]}")
```

- [ ] **Step 4: Add `transition_s` to `ensamblar` and branch in step 2** — change the signature:

```python
async def ensamblar(
    segmentos: list[SegmentoMontaje],
    audio_voiceover_key: str | None = None,
    output_key: str | None = None,
    duracion_objetivo: int | None = None,
    normalize_audio: bool = True,
    mute_clips: bool = False,
    transition_s: float = 0.0,
) -> tuple[str, float, bool]:
```

Then replace the concat block (the `if len(clip_paths) == 1: ... else: await _concat(...)`):

```python
        if len(clip_paths) == 1:
            clip_paths[0].rename(out_path)
        elif transition_s > 0:
            durations = [await _get_duration(c) for c in clip_paths]
            await _concat_xfade(clip_paths, durations, transition_s, out_path, mute=mute_clips)
        else:
            await _concat(clip_paths, out_path)
```

- [ ] **Step 5: Pass `transition_s` at the nota call site** — in `generar_pieza.py`, the `ensamblar(...)` call in `pieza_emision` (~line 741). It currently is:

```python
        video_key, duration, material_en_loop = await ensamblar(
            base_segs,
            duracion_objetivo=duracion_efectiva,
            normalize_audio=False,
            mute_clips=body.tipo_pieza in _MUTED_TYPES,
        )
```

Add `transition_s=_transition_for(body.tipo_pieza)` and import `_transition_for`. Update the import from `src.routes.montaje` to include `_transition_for` (it currently imports `SegmentoMontaje, _get_duration, _normalize_loudness, ensamblar`), and change the call to:

```python
        video_key, duration, material_en_loop = await ensamblar(
            base_segs,
            duracion_objetivo=duracion_efectiva,
            normalize_audio=False,
            mute_clips=body.tipo_pieza in _MUTED_TYPES,
            transition_s=_transition_for(body.tipo_pieza),
        )
```

- [ ] **Step 6: Run the transition tests + import check** —

```bash
cd apps/api && uv run pytest tests/unit/routes/test_montaje_transitions.py -v
uv run python -c "import src.routes.montaje; import src.routes.generar_pieza"
```
Expected: PASS (6 in the file); import exits 0.

- [ ] **Step 7: Run the full unit suite — no regressions** — `cd apps/api && uv run pytest tests/unit -q`
  Expected: PASS (existing tests, incl. `test_montaje_mute.py`, plus the new ones). `_concat` path is unchanged, so hard-cut pieces don't regress.

- [ ] **Step 8: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/montaje.py apps/api/src/routes/generar_pieza.py apps/api/tests/unit/routes/test_montaje_transitions.py
git commit -m "feat(montaje): crossfade transitions in ensamblar; nota passes transition_s"
```

---

## After all tasks

- **Live validation (manual, not unit-testable):** restart the API (no `--reload`, `127.0.0.1`) and render a real `nota` with several clips → confirm the cuts crossfade smoothly (video + audio) and the rest is unaffected. Render a `cola`/`vtr` → confirm hard cuts unchanged.
- Update `HANDOFF.md` (transitions done, what's validated).
- Use `superpowers:finishing-a-development-branch`. Branch `feat/grafismos-editables` (current; transitions ride on the same branch — or branch `feat/clip-transitions` off it first if you want them separable). PRs via GitHub URL; commit email `luiscbravo94@gmail.com`.
- Deferred (per spec): transitions for vtr/cola/total (pass `transition_s` for them); other transition styles (dip-to-black, wipe); per-cut variable durations.
