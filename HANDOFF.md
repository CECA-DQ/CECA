# HANDOFF

> Where the work stands right now, for whoever picks it up next. Updated on every commit.
> Keep it short and current — overwrite stale entries, don't accumulate history (git already has that).

**Branch:** `feat/multi-source-colas` (from `develop`) · **Updated:** 2026-05-31

## Current state
- **Multi-source colas (backend) DONE & validated live by the user** ("funciona"). cola/broll/total build correctly from 2-3 source videos: each clip routed to its own source. Per `docs/superpowers/plans/2026-05-31-multi-source-colas.md`.
- **Enabling fixes committed** (made multi-source work end-to-end from the front): `fuentes` http(s) URLs are downloaded (yt-dlp/ffmpeg), cached by URL hash (`_resolve_fuentes`/`_download_fuente`/`_storage_key_for`); montage routes clips to the **downloaded** storage key, not the URL; CORS dev ports `:3001`/`:3002`; `tests/unit/routes/test_resolve_fuentes.py` (9 tests). **87 unit tests green.**
- **Editable grafismos (backend) DONE** on branch `feat/grafismos-editables` (stacked on `feat/multi-source-colas`). 8 TDD tasks + holistic-review fixes, **109 unit tests green**. Reviewed per-task (spec + quality) + final holistic.
  - Restyled stepped cintillo (red inline tag + black title + white paragraph), person rótulo (white name + colour bar), new renderers (directo/contacto/reloj-Madrid/mosca-360/La 1), anchor presets + title-safe clamp + text-fit (no overflow), fade in/out in the burned MP4, `visible`/`obligatorio` fields, mandatory cintillo (title=`titular`, paragraph=`entradilla`).
  - `/api/grafismo/aplicar` renders an edited plan; `/preview-timeline` exposes it. Same plan drives front preview + backend burn.
  - **Not validated live yet** — needs a real cola + nota render check (use 127.0.0.1; restart API no-reload).
  - **Follow-ups (documented in plan):** auto-include corner furniture (mosca/clock/La 1) with cluster layout; free x/y; full slide+fade in MP4; ticking clock; `_PROMPT_GRAFISMOS` is dead code; cintillo start-time differs scored-vs-LLM path.
- Changes, all in `services/segment_selection.py` + one arg in `routes/generar_pieza.py`:
  1. The 3 `_build_*` builders preserve `fuente_index` (+ sentence dedup key now includes it).
  2. `_overlaps(a, b)` helper → overlap pruning in `_select_recurso`/`_select_non_overlapping` is source-aware (clips from different videos never falsely block each other).
  3. `select_segments(n_fuentes=…)`; nota/vtr with ≥2 sources fall back to per-frame (no unreliable cross-source sentence alignment). Route passes `n_fuentes=len(body.fuentes)`.
- Downstream (`_scored_segments_to_plan`, `_deduplicate_segments`, `_snap_segment_boundaries`, `fuentes[idx]→storage_key`) was already source-aware — untouched.
- **78 unit tests green** (62 prior + 16 new in `test_segment_selection_multisource.py`). Reviewed: spec + code quality per task, plus a final holistic review (Ready to merge).
- Frontend already sends `fuentes: [...]`, so no coordination commit needed.

## Next steps
- **Current work: crossfade transitions between clips in `nota`** (cuts are too abrupt). Spec written & approved: `docs/superpowers/specs/2026-05-31-clip-transitions-nota-design.md`. Design: add `transition_s` to `montaje.ensamblar`; when >0 (route passes 0.4 for nota) use a single `xfade`(video)+`acrossfade`(audio) filter chain instead of `_concat`; pure `_build_xfade_filter` helper. Next: writing-plans → execute.
- **Branch chain (all unmerged, stacked):** `develop` ← `feat/multi-source-colas` ← `feat/grafismos-editables` (current). Decide merge order before PRs. Editor "Option B" parked (see memory).
- PRs via the GitHub URL (no `gh` CLI); commit email `luiscbravo94@gmail.com`.

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
