# Design — Editable, animated, content-coherent grafismos (backend)

**Date:** 2026-05-31
**Status:** Approved (brainstorming) — pending implementation plan
**Scope:** Backend only. The interactive editor (add/remove/reposition live over the video) is the frontend's job, in its own repo. This spec covers the backend capabilities that enable it.

## Problem & current state (confirmed by reading the code)

Grafismos (TV on-screen graphics) are rendered in `src/routes/grafismo.py`: each element is drawn as a **full-frame RGBA PNG** (Pillow) and composited onto the final video with FFmpeg `overlay=0:0:enable='between(t,t0,t1)'` (`_apply_grafismos`, ~lines 434-501). Today:

- **7 types** exist: `titular` (cintillo), `rotulo_persona`, `rotulo_persona_simultaneo`, `dato`, `frase_clave`, `pie_pagina`, `mosca` (hardcoded "360" logo).
- **Position/size are hardcoded per type** (`_render_*` functions); the `posicion` field on `GrafismoElemento` is unused.
- **No animation** — visibility is a binary on/off via `enable='between(...)'`. No fades/slides.
- **Style** is navy box + orange accent — does **not** match the target broadcaster look.
- Grafismos are **backend-generated** in `pieza-emision` (`generar_timeline_narrativo` → `_scored_segments_to_plan` builds per-segment grafismo dicts; `_assign_grafismo_timings` + `_TIMING_RULES` set timing; `_plan_to_grafismos` converts to absolute time; `_apply_grafismos` burns them — PASO 4 of `pieza-emision`).
- Two relevant endpoints already exist: `/api/grafismo/aplicar` (low-level: `{video_input_key, elementos}` → burns), and `/api/montaje/preview-timeline` (returns the full plan with per-segment grafismos before render).
- Text is already **content-derived** (titular/nombre/cargo come from transcript + visual analysis); rotulos with no identified speaker are dropped.

The reference look the user wants (Spanish 24h news): a **stepped cintillo** (inline red "ÚLTIMA HORA" tag + black title band, white text + white paragraph band, black text, with a small gap between blocks), a **person rótulo** (white name + role on a colored bar), optional **Directo+location**, optional **WhatsApp contact**, and a corner **mosca** ("360" with a filled 0, above a black clock showing Spain time, plus the "La 1" channel mark). Nothing may bleed off-screen, and graphics should animate in/out.

## Goal & success criteria

- A single **grafismo plan** (JSON) is the source of truth: the backend generates it (coherent with the video), exposes it to the front (live CSS preview/editor), and burns it into the exported MP4 — so **what the editor shows equals what the local MP4 contains**.
- The burned render matches the **reference style** (palette, stepped cintillo, rótulo, mosca/clock) for **cola and nota**.
- **Mandatory** grafismos (always present): the black **title** band and the white **paragraph** band. **Optional** (shown only if enabled/available): `ÚLTIMA HORA` tag, person rótulo (only if a guest/speaker is identified), Directo+location, WhatsApp/contact, `360`, clock, `La 1`.
- **Nothing overflows the frame**: every box is clamped to a title-safe area; long auto-generated text fits (2-line clamp + `…`, or auto-shrink) and never bleeds.
- Graphics **animate**: in the front editor via CSS (instant), and in the exported MP4 via a simple **fade** in/out (FFmpeg) around each grafismo's window.
- Text stays **coherent with the video** (derived from analysis); the WhatsApp number is the only manual field.
- No regression to the existing pipeline; pure-Python geometry and FFmpeg-command construction are unit-testable without rendering.

## Decisions (from brainstorming)

- **Animation:** animated live in the front editor (CSS) **and** a simple fade in/out burned into the exported MP4 (FFmpeg). Full slide+fade in the MP4 is **out of scope** (deferred).
- **Positioning:** **fixed anchor presets per type** (TV-safe), not free x/y drag. (Free x/y deferred.)
- **Split:** the **front** builds the live editor (add/remove/toggle/reposition-by-anchor + animated preview); the **backend** provides the data model, the restyled burn-render, the default coherent plan, and a render-from-edited-plan endpoint.
- **Mandatory vs optional:** title band + paragraph band are mandatory; everything else is optional/toggleable.
- **Role-bar color** (yellow in one reference, blue in another, same person) is a **style parameter** of the rótulo (default per template), not content-derived.
- **Clock:** shows `Europe/Madrid` time, black background / white text. Baked as a **static stamp** at render time (deterministic, testable); a per-frame ticking clock is deferred.

## Architecture — the plan as single source of truth

```
video analysis ─► backend builds DEFAULT grafismo plan (coherent, restyled)
                        │  exposed via /preview-timeline (already exists)
                        ▼
        FRONT renders the plan as a CSS overlay on <video>
        (toggle / reposition-by-anchor / animated preview)  ── edits mutate the plan
                        │  on "export", front POSTs the edited plan
                        ▼
        BACKEND burns the plan into the MP4 (Pillow PNG + FFmpeg overlay + fade)
                        ▼
        exported MP4 = the local file with every grafismo baked in
```

The same plan drives both the front preview and the backend burn, so the editor and the exported file stay in sync. The front overlay is preview-only and is **not** part of the video file; the burn step is what produces the downloadable video.

## Data model

Extend `GrafismoElemento` (`grafismo.py`) — additive, backward-compatible:

- `obligatorio: bool = False` — title/paragraph are `True`; rest `False`.
- `visible: bool = True` — optional elements the user turned off are `False` (skipped at render).
- `ancla: str` — preset anchor per type (e.g. `cintillo_abajo_izq`, `rotulo_abajo_dcha`, `mosca_esquina_dcha`, `contacto_arriba_izq`, `directo_centro`). Replaces the unused `posicion`.
- `color_barra: str = ""` — optional override for the rótulo role-bar color (default per template).
- `anim: str = "fade"` — entrance/exit animation for the burned render.

The plan is the existing per-segment `grafismos` list plus these fields; `_scored_segments_to_plan` and `_assign_grafismo_timings` set `obligatorio`/`ancla`/timing; the front may flip `visible`, change `ancla` (to another allowed preset), or edit the WhatsApp text before export.

## Catalog & style

| Grafismo | Mandatory | Anchor (default) | Notes |
|---|---|---|---|
| Title band (black bg, white text) | ✅ | cintillo, bottom-left | from video |
| Paragraph band (white bg, black text, 2 lines) | ✅ | under title | from video |
| `ÚLTIMA HORA` tag (red, **inline** width) | optional | on top of title | breaking-news only |
| Person rótulo (white name + role on colored bar) | optional | bottom-right | only if speaker identified; bar color = param |
| Directo + location | optional | center, above cintillo | |
| Contact / WhatsApp | optional (manual) | top-left | only manual text field |
| `360` (filled 0) | optional | corner, above clock | program mark |
| Clock (black bg, white text, Madrid) | optional | corner, below `360` | static stamp |
| `La 1` | optional | corner, right of `360`/clock | channel mark |

Cross-cutting rules: replicate the reference palette (red `~#d12e2e`, black `#111`, white, role bar configurable, clock black/white); **title-safe clamp**; **text-fit** (2-line clamp + `…` / auto-shrink); small gap between the cintillo blocks (tag / title / paragraph). Exact hex values to be sampled from the reference image during implementation.

## Render changes (`src/routes/grafismo.py`)

- Restyle the `_render_*` functions to the reference look; add renderers for the stepped cintillo (tag+title+paragraph as one composed unit), Directo, contact, clock, `360`, `La 1`.
- Position each element from its **anchor preset** (a small table mapping `ancla` → coordinates within the safe area), instead of hardcoded per-type constants.
- **Fade:** instead of (or in addition to) the hard `enable='between(t0,t1)'`, fade each overlay in/out with the FFmpeg `fade` filter on alpha (`format=yuva420p, fade=in:…:alpha=1, fade=out:…:alpha=1`) around its `[t0,t1]`. Fade duration is a small constant (e.g. ~0.4s), capped so it never exceeds the element's visible window.
- **Safe-area + text-fit:** every rendered box is clamped within the title-safe rectangle; text wraps to ≤2 lines with `…`, names/roles auto-fit their box.
- Mandatory elements always render; optional render only when `visible` and their content exists.
- Clock text computed at render with `Europe/Madrid`.

## Coherence

Text comes from analysis as today (titular, párrafo, nombre, cargo, localización). If the speaker isn't identified, the person rótulo is omitted (never a placeholder name). The mandatory cintillo always carries the generated title/paragraph. Applies to **cola** and **nota** alike — both already receive a plan; the restyle and the mandatory/optional rules apply to both.

## Endpoints / flow

- `pieza-emision` keeps generating the default plan and burning it (PASO 4), now restyled + faded + anchored.
- Extend `/api/grafismo/aplicar` (or add `/api/montaje/render-grafismos`) to accept an **edited plan** + a base video key and return the burned MP4 — this is what the front's "export" calls.
- `/api/montaje/preview-timeline` continues to expose the plan (now with `obligatorio`/`visible`/`ancla`/color) for the front editor.

## Out of scope (deferred)

- Free x/y drag positioning (we ship anchor presets).
- Full slide+fade burned in the MP4 (we ship simple fade).
- The frontend editor itself (separate repo).
- Per-frame ticking clock (we ship a static Madrid stamp).
- Piece types beyond cola/nota for this round (the restyle is type-agnostic, but validation focuses on cola + nota).

## Testing

`tests/unit/` (pure geometry + command construction, no rendering/network):

- Anchor → coordinates fall inside the title-safe rectangle for every type, including with extreme-length text.
- Text-fit: long titular/párrafo clamp to ≤2 lines + `…`; long name/role fit their box.
- Mandatory always emitted; optional skipped when `visible=False` or content missing.
- Role-bar color param applied; default when unset.
- Clock string formatted in `Europe/Madrid`.
- Fade timing: fade-in/out windows computed within `[t0,t1]`, never exceeding it; the FFmpeg filter string contains the expected per-element fade.
- Plan round-trip: an edited plan (toggled `visible`, changed `ancla`) renders the expected element set.
