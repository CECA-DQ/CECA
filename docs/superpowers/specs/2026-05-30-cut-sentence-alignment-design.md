# Design — Sentence-aligned cut boundaries for video pieces

**Date:** 2026-05-30
**Status:** Approved (brainstorming) — pending implementation plan
**Depends on:** `feat/vision-candidate-moments` (transcript-guided frame sampling). This branch stacks on it.
**Scope:** Phase 2. Only the cut-boundary stage for speech/declaration piece types. Out of scope: diarization, b-roll types, the `analizar-material` / `generar-pieza` endpoints.

## Problem (confirmed with live evidence)

A live `pieza-emision` run on a real press conference produced (from the logs):
- `Visual scoring: 25 frames` (the new content-driven sampling — working) and
- `Segment selection [nota]: 19 candidates → 5 selected (60.0s / 60.0s target)`.

The 5 cuts are **fixed 12-second windows** built as `[ts-1.5, ts+clip_s-1.5]` around each scored frame's timestamp (`segment_selection.py:_build_per_frame_segments`), snapped to silence only within ±0.5s. The sentence spans (`t_start`/`t_end`) that `candidate_moments` computes are **discarded** — `analyze_video_visually` returns only scored `frames` (timestamp + score), so the cutter never sees sentence boundaries.

Result: clips start and end **mid-sentence** → abrupt, non-fluid cuts. This is the root cause of the user's "los cortes van bien pero no fluidos".

## Goal & success criteria

- For speech/declaration piece types, each cut is a **complete thought**: it starts at a sentence start and ends at a sentence end.
- Clip duration is **≈ the per-type target** (`clip_s`), reached by including whole consecutive sentences — not a hard fixed window.
- Cuts do not merge across a likely speaker turn (proxy: a transcript gap larger than `turn_gap`).
- No regression: b-roll/cola/off keep the current fixed-window behavior; if sentence data is unavailable, fall back to the fixed window.
- Pure-Python and deterministic → unit-testable without API calls.

## Decisions (from brainstorming)

- **Cut shaping:** expand to whole sentences up to ~`clip_s`, always ending on a sentence boundary.
- **Scope:** speech types `total, teaser, promo, vtr, nota, highlights`. `broll, cola, off` keep fixed windows.
- **Architecture:** recompute sentence units inside `segment_selection` from the `words` it already receives — do NOT thread candidate spans through Gemini (the candidate is one sentence; expansion needs neighbouring sentences anyway, so threading saves nothing and adds plumbing).

## Architecture

### 1. Shared helper `src/services/transcript_units.py` (DRY)

Move sentence-unit construction out of `candidate_moments.py` into a small shared module:

```
build_sentence_units(words: list[dict]) -> list[dict]
```

Returns units `{"t_start", "t_end", "text", "timestamp", "bounded_by_pause"}` (same shape as today's `_build_units`). `candidate_moments.py` imports and uses it (its internal `_build_units`/`_finalize_unit` are removed; its tests move to the new module's test). Behaviour is unchanged — this is a pure extraction.

### 2. `src/services/segment_selection.py` — sentence-aligned clip building

New constants:
```
_TURN_GAP_S = 1.0          # transcript gap that likely marks a speaker turn; stop expanding
_SPEECH_TYPES = {"total", "teaser", "promo", "vtr", "nota", "highlights"}
```

New helper:
```
_build_sentence_segments(candidates, units, clip_s, max_segment_s) -> list[dict]
```
For each scored frame (candidate) sorted by timestamp:
1. Find the unit whose `[t_start, t_end]` contains the frame `timestamp` (else the nearest unit). That unit's `t_start` is the clip start.
2. Expand forward: append following units while the next unit starts within `_TURN_GAP_S` of the current end AND cumulative duration `< clip_s` AND cumulative duration `< max_segment_s`. The clip end is the last included unit's `t_end`.
3. Edge case: if the first unit alone already exceeds `max_segment_s`, clamp `t_end = t_start + max_segment_s` (degenerate long sentence).
Each output segment keeps the frame's `max_score`, `hablante`, `cargo`, `razon` (same fields `_build_per_frame_segments` produces).

In `select_segments`, before the per-type selection step, choose the builder:
- if `tipo_pieza in _SPEECH_TYPES` and `units` are available → `_build_sentence_segments(candidates, units, clip_s, max_segment_s)`
- else → existing `_build_per_frame_segments(candidates, clip_s)` (unchanged).

`units = build_sentence_units(words or [])`. If `words` is empty (no transcript) → `units == []` → fall back to `_build_per_frame_segments` (no regression).

The downstream silence-snap (Step C) and per-type selection (`_select_for_total`, `_select_non_overlapping`, etc.) are unchanged: they operate on the already-aligned spans.

### 3. No changes to `visual_analysis.py` or the `pieza-emision` wiring

`select_segments` already receives `words` (`words_flat` in `pieza_emision`). Change is confined to `segment_selection.py` + the new shared module (+ the candidate_moments import update).

## Data flow

```
words ──► build_sentence_units ──► units (sentence spans)
scored frames ─┐
               └► _build_sentence_segments(frame.timestamp → enclosing unit, expand whole sentences ≤ clip_s, stop at turn_gap)
                        └► per-type selection (unchanged) ──► cuts that start & end on sentence boundaries
```

## Error handling

- `build_sentence_units([])` → `[]`.
- No enclosing/nearest unit for a frame (empty units) → that frame falls back to a fixed window via `_build_per_frame_segments` (the whole speech branch is skipped when `units == []`).
- All pure functions; no new I/O, nothing that can raise into the route beyond what exists today.

## Testing

`tests/unit/services/test_transcript_units.py` — the moved `_build_units` tests, now against `build_sentence_units` (punctuation split, silence split, empty input).

`tests/unit/services/test_segment_selection_sentences.py`:
- A frame inside a short sentence expands to include following sentences until ~`clip_s`, ending on a sentence boundary.
- Expansion stops when the gap to the next sentence exceeds `_TURN_GAP_S` (no cross-turn merge).
- Expansion stops at `max_segment_s`; a single over-long sentence is clamped.
- A `broll`/`cola` type still uses the fixed window (unchanged).
- Empty `words` → speech type falls back to the fixed window (no crash, no regression).

## Out of scope (later)

- Real diarization (we use `_TURN_GAP_S` as a turn-change proxy).
- Applying sentence sampling/alignment to `analizar-material` (still grid-12) and `generar-pieza` (text-only).
- Cross-fades / transitions between clips (this design only fixes cut placement, not visual transitions).
