# Design — Crossfade transitions between clips (nota mode)

**Date:** 2026-05-31
**Status:** Approved (brainstorming) — pending implementation plan
**Scope:** Backend `montaje` assembly only. Applies to `nota` pieces; everything else keeps hard cuts.

## Problem & current state (confirmed by reading the code)

`montaje.ensamblar` (`src/routes/montaje.py:239-330`) assembles a piece by: (1) normalising each clip to 1280×720 h264/aac (`_normalizar_clip`, 110-122), (2) joining them with `_concat` (125-140) — the **FFmpeg concat demuxer with `-c copy`** (stream copy, no re-encode), (3) loop if short, (4) mix voiceover, (5) loudness-normalise. Step 2 is a **hard cut**: clips are spliced end-to-end with no transition, so in `nota` (several distinct clips under a voiceover) the change between clips is abrupt.

Relevant context for `nota`: it is **not muted** (`mute_clips=False`) and carries a **voiceover** (`_PIECE_CONFIGS["nota"]["con_locucion"]=True`); the voiceover mix ducks clip audio to 15% (`_mix_voiceover`, 166-182), so audio continuity already comes from the voiceover — the clip audio is a quiet bed.

`pieza_emision` calls `ensamblar(base_segs, duracion_objetivo=…, normalize_audio=False, mute_clips=…)` (generar_pieza.py ~741); loudnorm runs later as a final step there.

## Goal & success criteria

- In `nota`, consecutive clips join with a short **crossfade/dissolve (~0.4s)** in **video and audio**, instead of a hard cut — the change feels fluid.
- Every other piece type and the standalone `/api/montaje/ensamblar` endpoint keep the current hard-cut behaviour (no regression).
- The transition logic is a **pure, unit-testable** filter-string builder (no FFmpeg execution needed to test the cumulative-offset math).
- No change to the downstream loop / voiceover / loudnorm steps.

## Decisions (from brainstorming)

- **Transition:** crossfade/dissolve (`xfade=transition=fade`), duration **0.4s**.
- **Audio:** crossfade audio too (`acrossfade`, same 0.4s) — the ducked clip audio under the voiceover means no speech muddiness.
- **Scope:** `nota` only for now (vtr/cola/total/etc. unchanged).

## Architecture — one parameter, one new path

Add `transition_s: float = 0.0` to `ensamblar`:
- `transition_s == 0.0`, **or** fewer than 2 clips, **or** `mute_clips=True` → the **existing `_concat` path, unchanged** (hard cut, stream copy).
- `transition_s > 0` with ≥2 clips and audio → the **new crossfade path** (below).

`pieza_emision` sets a module constant `_TRANSITION_S = 0.4` and passes `transition_s=_TRANSITION_S if body.tipo_pieza == "nota" else 0.0`. The standalone `/ensamblar` endpoint calls with the default `0.0`. So the per-type decision lives in the route; `ensamblar` stays generic.

## The crossfade path (one FFmpeg call)

After normalising all clips (unchanged), **probe each clip's duration** (`_get_duration`) and build **a single `filter_complex`** with every normalised clip as an input — no pairwise re-encode:

- **Video — chained `xfade` with cumulative offsets.** For durations `[d0..d(n-1)]` and `T=transition_s`:
  `running = d0`; for each `j` in `1..n-1`: `offset_j = running − T`; emit `xfade=transition=fade:duration=T:offset=offset_j`; `running += d_j − T`.
  Output video duration = `Σdᵢ − (n−1)·T`.
- **Audio — chained `acrossfade=d=T`.** `acrossfade` overlaps the tail of input k with the head of input k+1 (no offset arg) and shrinks the total by `T` per join — matching the video chain, so audio and video stay in sync.
- One re-encode (h264/aac), then map the final `[v]`/`[a]`.

**FFmpeg gotcha (implementation note):** `xfade` requires all inputs to share pixel format, fps, SAR and timebase, or it errors with "inputs must have the same format". Even though `_normalizar_clip` already re-encodes to 1280×720/25fps/yuv420p, prefix each video input in the filter with `fps=25,format=yuv420p,setsar=1,settb=AVTB` (and resample audio consistently) before the `xfade`/`acrossfade` chain so it never fails on edge-case source metadata.

Extract a **pure helper `_build_xfade_filter(durations: list[float], transition_s: float) -> str`** that returns the `filter_complex` string (video xfade chain + audio acrossfade chain). This is the unit-tested core (mirrors the existing `_build_overlay_filter` pattern in `grafismo.py`).

## Edge cases (guards)

- **1 clip:** no transition — the single clip is the output (existing behaviour).
- **Clip shorter than `2·T`:** clamp `T = min(transition_s, shortest_clip / 2)` so offsets never go negative. (`nota` clips are ≥ `min_segment_s` = 5s, so this is defensive.)
- **`mute_clips=True`:** video-only `xfade`, no `acrossfade` (not applicable to `nota`, which keeps audio; defensive).

## Downstream — unchanged

Loop-if-short, voiceover mix, and loudnorm run on the assembled output exactly as today. Total duration shrinks by `(n−1)·0.4s` (e.g. 5 clips → 1.6s), well within the loop threshold (`< 40%` of target).

## Out of scope (deferred)

- Transitions for other piece types (vtr/cola/total) — easy to enable later by passing `transition_s` for them.
- Other transition styles (dip-to-black, wipe) — only crossfade now.
- Per-cut variable transition durations — a single constant for now.

## Testing

`tests/unit/routes/test_montaje_transitions.py`:
- `_build_xfade_filter` with 2 clips → one `xfade` at the right offset (`d0 − T`) + one `acrossfade=d=T`.
- 3 clips → two `xfade`s with cumulative offsets (`d0−T`, then `d0+d1−2T`) + two `acrossfade`s.
- Filter contains `transition=fade` and `duration=0.40` / `d=0.40` for `T=0.4`.
- 1 clip / empty → no `xfade` emitted (builder returns empty or is not invoked).
- (Route) `ensamblar` with `transition_s=0.0` takes the concat path (no `xfade` in the command) — no regression; `pieza_emision` passes `0.4` only for `nota`.
