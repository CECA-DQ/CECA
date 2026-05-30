# HANDOFF

> Where the work stands right now, for whoever picks it up next. Updated on every commit.
> Keep it short and current — overwrite stale entries, don't accumulate history (git already has that).

**Branch:** `docs/split-claude-md` · **Updated:** 2026-05-30

## Current state
- `CLAUDE.md` slimmed (812 → 75 lines) into a lean index + on-demand `docs/` (architecture, pipeline, adapters, multitenancy, conventions, DECISIONS/). Pushed; **PR pending** → base `develop`.
- Project runs end to end locally: postgres :5433 + redis :6379 (docker), migrations `0001`→`0007` applied, API on :8000 (`/healthz` ok), Celery worker ready.

## Next steps
- Open the PR for `docs/split-claude-md` → `develop`.
- (Done) `greenlet` dependency fix committed; embeddings note in `docs/adapters.md` corrected (no 384-vs-768 mismatch — column is 384).

## Gotchas
- Use Python **3.12** for the venv (uv may pick 3.14, which lacks a `greenlet` wheel → migrations crash).
- Use `127.0.0.1`, not `localhost`, for the API on macOS.
- Background API/worker stop when the session closes; relaunch them. Docker survives.

## How to run
See `docs/architecture.md` and the **Key commands** in `CLAUDE.md`.
