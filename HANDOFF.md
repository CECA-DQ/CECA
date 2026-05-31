# HANDOFF

> Where the work stands right now, for whoever picks it up next. Updated on every commit.
> Keep it short and current — overwrite stale entries, don't accumulate history (git already has that).

**Branch:** `feat/multi-source-colas` (from `develop`) · **Updated:** 2026-05-31

## Current state
- All prior work (PRs #1–#6) is **merged into `develop`**: lean CLAUDE.md split, STT timeout, log visibility, vision candidate-moments, sentence-aligned cuts, and the muted recurso **cola**.
- **Starting multi-source colas (backend).** Design written, verified line-by-line
  against the code, and approved:
  `docs/superpowers/specs/2026-05-31-multi-source-colas-design.md`. No code yet.

## Next steps
- **Plan written:** `docs/superpowers/plans/2026-05-31-multi-source-colas.md` (4 TDD tasks). Executing now via subagent-driven-development.
- The fix, all in `services/segment_selection.py` + one arg in `routes/generar_pieza.py`:
  1. Preserve `fuente_index` in the 3 `_build_*` segment builders (the single drop point).
  2. Make overlap checks source-aware (`_select_recurso`, `_select_non_overlapping`) so clips from different videos don't falsely block each other.
  3. Add `n_fuentes` to `select_segments`; speech types (nota/vtr) with ≥2 sources fall back to per-frame.
- Verified already source-aware (no change): `narrative_timeline._scored_segments_to_plan`, `_deduplicate_segments`, `_snap_segment_boundaries`, the `fuentes[idx] → storage_key` mapping.

## Deferred (documented in the spec)
- Per-source word-offset so multi-source nota/vtr can be **sentence-aligned** (not just per-frame). Needs its own spec.
- `off` → recurso selection (keeps a voiceover, so not muted).
- Cola cuts varied 3-6s; balanced per-source distribution (round-robin).

## Gotchas
- Use Python **3.12** for the venv (uv may pick 3.14, which lacks a `greenlet` wheel → migrations crash).
- Use `127.0.0.1`, not `localhost`, for the API on macOS.
- Background API/worker stop when the session closes; relaunch them. Docker survives.

## How to run
See `docs/architecture.md` and the **Key commands** in `CLAUDE.md`.
