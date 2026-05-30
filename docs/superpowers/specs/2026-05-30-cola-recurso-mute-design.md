# Design — Make "cola" pieces real: recurso footage + muted audio

**Date:** 2026-05-30
**Status:** Approved (brainstorming) — pending implementation plan
**Depends on:** `feat/cut-sentence-alignment` (and below it, `feat/vision-candidate-moments`). This branch stacks on top.
**Scope:** the `cola` piece type (and `broll`) in the vision path (`pieza-emision`). Out of scope: `off` (has voiceover), archive b-roll, the text-only `generar-pieza` path.

## Problem (confirmed with live evidence + code)

A live `pieza-emision` with `tipo_pieza="cola"` produced (logs): `Segment selection [cola]: 23 candidates → 7 selected`. The user reports the result **looks and sounds like a `nota`**: it shows the speaker talking, with full original audio. Root causes in code:

1. **Selection picks the speaker, not recurso.** `select_segments` for `cola` falls into the generic Step D branch that sorts by `max_score` **descending** — and Gemini scores "active declaration" highest. So a cola selects the same speaker-declaration frames a nota would (`segment_selection.py`, Step D `else`).
2. **Audio is never type-aware.** `_normalizar_clip` always re-encodes with `-c:a aac` (keeps original audio); for `cola` (`con_locucion=False`) there is no voiceover to mix, so the declarante's audio plays at full volume (`montaje.py:_normalizar_clip`, `ensamblar`).

A real cola is **recurso footage (no talking-head declaration) that the presenter narrates over live** — so it must show non-speaker shots and carry **no declarante audio**.

## Goal & success criteria

- A `cola` selects **recurso** frames (no active declaration), spread across the video.
- A `cola` clip carries **no audio** (silent) — the presenter narrates live.
- Same input as a nota (URL / uploaded video, same front flow) — only processing differs by `tipo_pieza`.
- No regression for nota/vtr/total/etc. (their selection + audio unchanged).
- Always yields several distinct takes spread across the video: recurso is preferred, then **topped up with the remaining (muted) frames — also spread —** to fill the target, so a low-recurso source never collapses to a single looped clip. (Earlier "pure recurso only" produced a 1-clip loop on talking-head sources; the user chose top-up.) Never crash.
- Pure-Python selection logic → unit-testable.

## Decisions (from brainstorming)

- **Footage source:** the same video (no archive). Recurso = the non-declaration frames already scored by Gemini.
- **Audio:** muted total (silent).
- **Selection:** prefer non-speaker / low-declaration frames, spread across the timeline; fallback to least-declaration.

## Architecture

### 1. Recurso-first selection — `src/services/segment_selection.py`

New constants:
```
_RECURSO_TYPES = {"cola", "broll"}   # b-roll recurso, no narration → recurso frames, muted
_RECURSO_MAX_SCORE = 4               # Gemini band 1-4 = listening / wide / no active speech
```

New helper `_select_recurso(segments, target_duration, max_segs)`:
1. **recurso** = segments where `hablante in ("plano_sala", "desconocido", "")` **or** `max_score <= _RECURSO_MAX_SCORE`.
2. Order recurso chronologically and pick spread across the timeline (reuse the existing minimum-spacing idea) until cumulative duration reaches `target_duration` (respecting `max_segs`).
3. **Top up to fill the target**: after adding recurso, append the remaining frames (`_spread` across the timeline) until the target is reached. cola/broll are muted, so these topped-up frames carry no audio. This guarantees several distinct takes instead of a single looped clip on low-recurso sources. Recurso is always added first (preferred); a `clip_s` of 5s gives more, punchier takes.
Returns segments sorted chronologically (same dict shape as the other builders).

In `select_segments` Step D, add a branch: `if tipo_pieza in _RECURSO_TYPES → result = _select_recurso(segments, target_duration, max_segs)`; all other types unchanged. (Step A/B/C unchanged; `cola`/`broll` still use `_build_per_frame_segments` in Step B — they are not speech types.)

### 2. Muted clips — `src/routes/montaje.py`

- `_normalizar_clip(source, t_start, t_end, out, mute: bool = False)`: when `mute`, replace the audio args (`-c:a aac ...`) with `-an` (drop the audio stream).
- `ensamblar(..., mute_clips: bool = False)`: pass `mute=mute_clips` into each `_normalizar_clip` call. With no audio track, the existing voiceover-mix and loudness-normalization steps are already guarded (no-op / try-except), so they stay correct.

### 3. Wire it in `pieza_emision` — `src/routes/generar_pieza.py`

- Call `ensamblar(base_segs, duracion_objetivo=..., normalize_audio=False, mute_clips=(body.tipo_pieza in {"cola", "broll"}))`.
- Verify the post-assembly graphics step `_apply_grafismos` does not re-introduce an audio track (it overlays graphics on the video; a muted input must stay muted). If `_apply_grafismos` maps audio explicitly, ensure it tolerates / preserves no-audio. (Pin exact behavior in the plan.)

## Data flow

```
scored frames (hablante, puntuacion) ─► select_segments[cola] ─► _select_recurso
        (prefer plano_sala / score≤4, spread; fallback lowest-score) ─► recurso cuts
recurso cuts ─► ensamblar(mute_clips=True) ─► _normalizar_clip(-an) ─► concat (silent)
        ─► grafismos (silent preserved) ─► final cola video (no declarante audio)
```

## Error handling

- `_select_recurso` with no recurso and no segments → returns `[]` (caller already handles empty).
- Muting: `-an` cannot fail on a video; downstream audio steps already tolerate a missing audio track.
- Never raises into the route beyond what exists today.

## Testing

`tests/unit/services/test_segment_selection_recurso.py`:
- Frames mixing speaker (high score) and recurso (`plano_sala` / score≤4): `_select_recurso` picks the **recurso** ones, not the high-score speaker frames.
- Fallback: all frames are high-score speaker → returns the **lowest-score** ones (least declaration), not the top.
- Spread: selected timestamps are distributed (min spacing respected), capped at `target_duration`/`max_segs`.
- `select_segments(tipo_pieza="cola", ...)` routes through `_select_recurso`; `tipo_pieza="nota"` is unchanged.

Montaje (light, since it's a subprocess wrapper): a unit test asserting `_normalizar_clip(..., mute=True)` builds an ffmpeg command containing `-an` and not `-c:a` (inspect the command list — refactor to make it inspectable if needed), or a signature test that `ensamblar` accepts `mute_clips`.

## Out of scope (later)

- `off` pieces (recurso **with** voiceover). `off` is editorially recurso too and should later route through `_select_recurso` (better frames), but it carries a voiceover (audio is mixed, not muted) so it's a separate case — deferred. Today `off` stays on the score-descending `else` branch.
- Pulling recurso from the archive (`segmentos_archivo`) when the source has too little — deferred.
- The text-only `generar-pieza` endpoint (could get the same `mute_clips` later for consistency).
