# HANDOFF

> Where the work stands right now, for whoever picks it up next. Updated on every commit.
> Keep it short and current — overwrite stale entries, don't accumulate history (git already has that).

**Branch:** `feat/cut-sentence-alignment` · **Updated:** 2026-05-30

## Current state
- `select_segments` now routes speech/declaration piece types (`_SPEECH_TYPES`) through `_build_sentence_segments` when a transcript (`words`) is present; falls back to the fixed-window / merged builder for b-roll types or when `words` is empty.
- All 47 unit tests green; 9 tests in `test_segment_selection_sentences.py` cover the new routing (e2e), edge cases (empty candidates, ts-after-all-units nearest fallback, multiple candidates), and original sentence expansion/gap/clamp behaviour.
- `_build_sentence_segments` docstring updated with sorted-units assumption.
- Commit: `ed0ac0a feat(segment-selection): sentence-aligned cuts for speech types, fixed-window fallback`

## Next steps
- Open PR for `feat/cut-sentence-alignment` → `develop`.
- Continue `docs/split-claude-md` PR (previously pending) — base `develop`.

## Gotchas
- Use Python **3.12** for the venv (uv may pick 3.14, which lacks a `greenlet` wheel → migrations crash).
- Use `127.0.0.1`, not `localhost`, for the API on macOS.
- Background API/worker stop when the session closes; relaunch them. Docker survives.

## How to run
See `docs/architecture.md` and the **Key commands** in `CLAUDE.md`.
