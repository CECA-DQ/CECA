# HANDOFF

> Where the work stands right now, for whoever picks it up next. Updated on every commit.
> Keep it short and current — overwrite stale entries, don't accumulate history (git already has that).

**Branch:** `feat/cut-sentence-alignment` · **Updated:** 2026-05-30

## Current state
- `select_segments` now routes speech/declaration piece types (`_SPEECH_TYPES`) through `_build_sentence_segments` when a transcript (`words`) is present; falls back to the fixed-window / merged builder for b-roll types or when `words` is empty.
- All 48 unit tests green; `test_segment_selection_sentences.py` covers the new routing (e2e), edge cases (empty candidates, ts-after-all-units nearest fallback, multiple candidates, dedup of identical spans), and sentence expansion/gap/clamp behaviour.
- `_build_sentence_segments` docstring updated with sorted-units assumption.
- Commit: `ed0ac0a feat(segment-selection): sentence-aligned cuts for speech types, fixed-window fallback`

## Next steps
- Open PR for `feat/cut-sentence-alignment` → `develop` (sentence-aligned cuts; validated single-source).
- Open PRs for the other pushed branches: `fix/stt-timeout`, `chore/logging-visibility` (both → `develop`).
- **Follow-up (multi-source, deferred):** today pieces use 1 source. When combining >1 source, cut timing breaks — each source is transcribed from 0.0 and `pieza_emision` flattens `words` with no offset (non-monotonic timeline), and the builders drop `fuente_index`. Fix = offset each source's `words` before flattening + thread `fuente_index` through the segment builders so clips route to the right source.

## Gotchas
- Use Python **3.12** for the venv (uv may pick 3.14, which lacks a `greenlet` wheel → migrations crash).
- Use `127.0.0.1`, not `localhost`, for the API on macOS.
- Background API/worker stop when the session closes; relaunch them. Docker survives.

## How to run
See `docs/architecture.md` and the **Key commands** in `CLAUDE.md`.
