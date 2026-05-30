# HANDOFF

> Where the work stands right now, for whoever picks it up next. Updated on every commit.
> Keep it short and current — overwrite stale entries, don't accumulate history (git already has that).

**Branch:** `feat/cola-recurso` (stacked on the chain below) · **Updated:** 2026-05-31

## Current state
- **COLA fixed & validated live:** cola/broll now select **recurso** (non-speaker / low-score) clips, **muted** (`-an`), 5s cuts, **spread across the timeline**, and **top up with muted takes** when recurso is scarce so a talking-head source yields several distinct takes (never a 1-clip loop). User confirmed it works.
- 62 unit tests green. Cola logic in `segment_selection._select_recurso` + `_MUTED_TYPES` wiring in `pieza_emision`; audio via `montaje._normalize_cmd(mute=…)`.
- Branch stack (each → `develop`, merge in order): `fix/stt-timeout`, `chore/logging-visibility`, `feat/vision-candidate-moments`, `feat/cut-sentence-alignment`, `feat/cola-recurso`.

## Next steps
- **Push `feat/cola-recurso`** (committed `01b1b48`, NOT pushed yet) and open its PR.
- Open PRs and merge **in order**: `fix/stt-timeout` + `chore/logging-visibility` (independent) → `feat/vision-candidate-moments` → `feat/cut-sentence-alignment` → `feat/cola-recurso`.
- Delete throwaway local branches `tmp/verify`, `tmp/verify-cola`.
- **Deferred follow-ups:** (1) multi-source — each source transcribed from 0.0, `pieza_emision` flattens `words` with no offset + builders drop `fuente_index`; fix = offset words + thread fuente_index. (2) `off` should also use recurso selection (it keeps a voiceover, so not muted). (3) cola cuts could be varied 3-6s (needs lowering the 5s `min_segment_s` floor).

## Gotchas
- Use Python **3.12** for the venv (uv may pick 3.14, which lacks a `greenlet` wheel → migrations crash).
- Use `127.0.0.1`, not `localhost`, for the API on macOS.
- Background API/worker stop when the session closes; relaunch them. Docker survives.

## How to run
See `docs/architecture.md` and the **Key commands** in `CLAUDE.md`.
