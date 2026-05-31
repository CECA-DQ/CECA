# Design — Multi-source colas (backend)

**Date:** 2026-05-31
**Status:** Approved (brainstorming) — pending implementation plan
**Scope:** Backend only. The frontend already sends `fuentes: [storage_key, ...]`.

## Problem (confirmed by reading the code)

`POST /api/montaje/pieza-emision` accepts `fuentes: list[str]` and analyzes each
source separately. Every scored frame is correctly tagged with its source at
`routes/generar_pieza.py:647-651` (`{**f, "fuente_index": i}`). The final mapping
is also correct: `generar_pieza.py:728-738` maps `fuente_index → fuentes[idx] →
SegmentoMontaje.storage_key`, and `montaje.ensamblar` cuts each clip from its own
`storage_key`.

**`fuente_index` is dropped in the middle.** The three segment builders in
`services/segment_selection.py` rebuild the segment dict and omit `fuente_index`:

- `_build_per_frame_segments` (lines 69-85) — used by cola/broll and by the
  multi-source speech fallback.
- `_build_sentence_segments` (98-147) — speech types, single source.
- `_build_merged_segments` (150-197) — currently unreachable (all types have
  `per_frame=True`), kept for reference.

None of the *selectors* (`_select_recurso`, `_select_non_overlapping`,
`_select_for_total`, the highlights/teaser/promo branches) rebuild the dict — they
pass segment dicts through. So preserving `fuente_index` in the builders is
sufficient for it to reach the plan.

**Second, independent bug — single-timeline assumptions.** Several functions
compare `t_start`/`t_end` assuming one timeline. With 2-3 sources each timestamped
from `0.0`, two clips from *different* videos at overlapping local times look like
a false overlap:

- `_select_recurso._try_add` (line 266) — rejects the second clip as overlapping.
  This is the cola path; it would *reduce* variety exactly where multi-source
  should *increase* it.
- `_select_non_overlapping` (215-218) — same false-overlap for the multi-source
  speech fallback.

**Already source-aware (verified, no change needed):**

- `narrative_timeline._scored_segments_to_plan` (line 606) copies
  `fuente_index` from each scored segment into the plan segment.
- `generar_timeline_narrativo` (line 643-644) uses the deterministic
  `_scored_segments_to_plan` path — no LLM decides cuts — when `scored_segments`
  is provided (our path).
- `generar_pieza._deduplicate_segments` (70-91) dedups by
  `(fuente_index, tiempo_inicio, tiempo_fin)`.
- `generar_pieza._snap_segment_boundaries` (94-135) builds per-source cut points
  and snaps each segment using its own source's words (`source_cuts[src_idx]`,
  line 119). This per-source snap supersedes the single-timeline silence-snap done
  inside `select_segments` Step C.

## Goal & success criteria

- For cola/broll/total, each selected clip is routed to the correct source video:
  `SegmentoMontaje.storage_key == fuentes[clip.fuente_index]`.
- Multi-source cola yields clips from *several* sources (variety), not all from
  source 0, and source A clips never block source B clips during overlap pruning.
- nota/vtr with ≥2 sources degrade gracefully to per-frame selection (correct
  source routing, visual cuts instead of sentence-aligned) — never silently wrong
  sentence boundaries.
- No regression for single-source pieces (`n_fuentes == 1` keeps today's behaviour
  exactly, including sentence alignment for speech types).
- All changes are pure functions, unit-testable without API calls.

## Decisions (from brainstorming)

- **Scope:** cola/broll/total now. nota/vtr multi-source = per-frame fallback.
  The per-source word-offset fix (needed for *sentence-aligned* multi-source
  speech) is **deferred** — see Out of scope.
- **Where to preserve `fuente_index`:** in the builders (the single drop point),
  not via a parallel structure or downstream reconstruction.
- **How to detect multi-source:** an explicit `n_fuentes: int = 1` parameter on
  `select_segments`, not derived from the frames (robust, testable, default keeps
  existing callers unchanged).
- **Speech guard with ≥2 sources:** falls back to per-frame (not reject, not
  allow-with-warning).

## Architecture

All code changes are in `services/segment_selection.py`, plus one argument at the
call site in `routes/generar_pieza.py`. No change to `montaje.py`,
`narrative_timeline.py`, or the route's plan-mapping/dedup/snap helpers.

### Change (a) — preserve `fuente_index` in the builders

In `_build_per_frame_segments`, `_build_sentence_segments`, and
`_build_merged_segments`, add to each emitted segment dict:

```python
"fuente_index": f.get("fuente_index", 0),
```

(`f` is the scored frame; `fuente_index` was set on it at the route. Frames that
lack it — e.g. single-source pipeline — default to 0.)

In `_build_sentence_segments`, the dedup that collapses identical spans uses the
key `(s["t_start"], s["t_end"])`. Extend it to
`(s["fuente_index"], s["t_start"], s["t_end"])` so two sources' identical local
spans are not collapsed into one. (Defensive: the speech guard means multi-source
never reaches this builder, but the key should be correct regardless.)

### Change (b) — source-aware overlap

Add a helper at module level:

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

Use it in:

- `_select_recurso._try_add` — replace the inline
  `any(seg["t_start"] < s["t_end"] and seg["t_end"] > s["t_start"] for s in selected)`
  with `any(_overlaps(seg, s) for s in selected)`.
- `_select_non_overlapping` — replace its inline overlap check the same way.

`_build_merged_segments` is unreachable (dead code); it gets `fuente_index` for
consistency but its time-based merge is **not** made source-aware (YAGNI; documented
as a known limitation if it is ever re-enabled).

### Change (c) — speech guard

Add `n_fuentes: int = 1` to `select_segments`. In Step B, build sentence units only
for single-source speech:

```python
units = (
    build_sentence_units(words or [])
    if tipo_pieza in _SPEECH_TYPES and n_fuentes < 2
    else []
)
```

With `n_fuentes >= 2`, speech types fall through to `_build_per_frame_segments`
(they have `per_frame=True`). Step D still routes vtr/nota to
`_select_non_overlapping`, which is now source-aware.

At the call site (`generar_pieza.py:657-662`), pass:

```python
score_selected = select_segments(
    scored_frames_flat,
    target_duration=float(duracion_efectiva),
    words=words_flat,
    tipo_pieza=body.tipo_pieza,
    n_fuentes=len(body.fuentes),
)
```

## Data flow (after the fix)

```
per-source analysis ─► scored frames, each tagged fuente_index (route 647-651)
        │
        ▼
select_segments(n_fuentes)
   Step A filter ─► candidates keep fuente_index
   Step B build  ─► builders COPY fuente_index;
                    speech+multi-source ─► per-frame (guard)
   Step C snap   ─► single-timeline (superseded later by per-source snap)
   Step D select ─► source-aware overlap; dict passed through with fuente_index
        │
        ▼
_scored_segments_to_plan (copies fuente_index, line 606)
        ▼
_deduplicate_segments (source-aware) ─► _snap_segment_boundaries (per-source)
        ▼
SegmentoMontaje(storage_key = fuentes[fuente_index]) ─► ensamblar cuts the right video
```

## Error handling

- Frame without `fuente_index` → defaults to 0 (single-source behaviour).
- `fuente_index` out of range → already clamped at `generar_pieza.py:730-731`
  (`if idx >= len(body.fuentes): idx = 0`) and in
  `narrative_timeline._validate_plan` (`min(idx, n_fuentes-1)`).
- No new exceptions can reach the route; all changed functions are pure.

## Testing

`tests/unit/services/test_segment_selection_multisource.py`:

- Each builder (`_build_per_frame_segments`, `_build_sentence_segments`,
  `_build_merged_segments`) copies `fuente_index` from the frame to the segment.
- `_build_sentence_segments` keeps two same-span clips from different sources
  distinct (dedup key includes `fuente_index`).
- `_select_recurso`: two clips from different sources with overlapping local time
  ranges are BOTH kept; two clips from the same source that overlap → one dropped.
- `_select_non_overlapping`: same source-aware overlap behaviour.
- `select_segments` guard: a speech type with `n_fuentes>=2` produces per-frame
  segments (not sentence-aligned) and preserves `fuente_index`; with `n_fuentes==1`
  it still sentence-aligns (no regression).
- `select_segments` end-to-end on scored frames with mixed `fuente_index` → output
  segments carry the correct `fuente_index`.

The 62 existing unit tests must stay green (`n_fuentes` defaults to 1).

## Out of scope (deferred)

- **Per-source word-offset for sentence-aligned multi-source speech (nota/vtr).**
  Each source is transcribed from `0.0` and `words_flat` flattens all sources with
  no offset, so a cross-source sentence timeline is non-monotonic. Making
  multi-source nota/vtr *sentence-aligned* (rather than the per-frame fallback this
  spec ships) needs offsetting each source's words and tagging them with their
  source. Its own spec.
- **`off`** keeps a mixed voiceover (not muted) and still uses the generic
  score-fill branch; switching it to recurso selection is a separate follow-up.
- **Balanced per-source distribution** (round-robin so each source contributes
  roughly equally). The current `_spread` already gives variety and more sources
  means more candidates; a guaranteed per-source quota is a future enhancement.
- **`_build_merged_segments` source-aware merge** — unreachable today; only if it
  is re-enabled.
