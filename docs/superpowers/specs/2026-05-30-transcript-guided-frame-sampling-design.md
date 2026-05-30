# Design — Transcript-guided frame sampling for video moment selection

**Date:** 2026-05-30
**Status:** Approved (brainstorming) — pending implementation plan
**Scope:** Phase 1 only. Phase 2 (audio-emphasis) and the text-only endpoints are out of scope.

## Problem

The vision-first path scores video frames with Gemini and selects cuts from those scores. Frame sampling is a **blind uniform time grid** capped at **12 frames for the whole video** (`visual_analysis.py:122-130`): `max_frames = min(max(4, duration/5), 12)`, `interval = max(5, duration/max_frames)`.

Consequences:
- A 24-min press conference → 12 frames, one every ~120 s → ~0.03% of footage scored. Great 3-second moments between samples are never scored, so they can never be selected.
- Cuts are anchored to the arbitrary sampled instant (`segment_selection.py:71`: `[ts-1.5, ts+clip_s-1.5]`), not to the real soundbite boundary.
- Frames are 320×180 (`visual_analysis.py:75`) — too low to read documents / expressions / on-screen text the scoring prompt asks for.

Root cause (altitude): sampling is decoupled from content. The fix is to **drive sampling from the transcript** (which we already have, densely, with word-level timestamps), not from a fixed clock.

## Goal & success criteria

- Frames are scored at content-relevant moments, not a blind grid.
- Bounded cost: **≤ ~25 frames per video**, 1–2 Gemini calls (per user budget decision).
- Cuts anchor to sentence boundaries.
- No regression: if the detector yields nothing or errors, fall back to today's grid behavior.
- Pure-transcript and deterministic → unit-testable without API calls.

## Signals (agreed)

Reliable from transcript (Phase 1): **complete sentences/soundbites** and **numbers/dates/keywords**. Deferred: **speaker changes** (Whisper gives no diarization and `_transcribe_source` drops `speaker`; instead reuse Gemini's per-frame `hablante` *after* scoring, as `segment_selection` already does) and **emphasis/tension** (needs audio energy → Phase 2).

## Architecture

### 1. New module `src/services/candidate_moments.py` (single responsibility)

```
find_candidate_moments(
    words: list[dict],          # [{"start","end","word"}]
    segments: list[dict],       # [{"start","end","text"}]
    duration: float,
    tema: str = "",
    budget: int = 25,
) -> list[CandidateMoment]
```

`CandidateMoment = {"timestamp": float, "t_start": float, "t_end": float, "text": str, "score": float, "source": "sentence"|"grid"}`

Algorithm:
1. **Build sentence units** from `words`: group consecutive words, breaking on sentence-final punctuation (`.`, `?`, `!`) and on silence gaps ≥ threshold (reuse `_find_silences` logic from `segment_selection`). Each unit gets `t_start`, `t_end`, `text`, and a representative `timestamp` (unit midpoint).
2. **Heuristic score** per unit (cheap, 0..N): `+` contains numbers/dates (regex), `+` contains a `tema` keyword, `+` reasonable length (within [min,max] words/seconds), `+` bounded by a real pause.
3. **Rank** by score desc; take the top `floor(budget * 0.8)`, enforcing a **minimum spacing** so two candidates aren't adjacent.
4. **Grid floor**: add up to `ceil(budget * 0.2)` evenly-spaced grid timestamps not already near a chosen candidate (coverage safety net for low-talk sections).
5. Return ≤ `budget` candidates sorted chronologically.

Falls back to `[]` on any internal error (caller then uses the grid).

### 2. `src/services/visual_analysis.py` (surgical change)

- `analyze_video_visually(...)` gains optional `candidate_moments: list[CandidateMoment] | None = None`.
- If provided and non-empty: extract **one frame per candidate at its `timestamp`** (single-frame ffmpeg `-ss` seeks, or a select filter), at **512×288** (up from 320×180). The interleaved transcript window per frame uses the candidate's own `text`.
- If absent/empty: **unchanged** grid behavior (backward compatible).
- **Fix the score↔timestamp alignment bug** (`:198`): instead of `frame["timestamp_s"] = raw_frames[i][0]` by blind index, validate `len(scored) == len(frames_sent)` and map each scored frame back to its source timestamp by `frame_id`. If counts mismatch, log and align only the frames we can trust.

### 3. `src/routes/generar_pieza.py` — `pieza-emision` wiring

After transcription (we already have `all_words_trans` / `all_segs_trans`), call `find_candidate_moments(...)` per source and pass the result into `analyze_video_visually(..., candidate_moments=...)`.

### 4. Robustness

Keep the "never crashes the pipeline" philosophy: detector failure or empty result → grid fallback; Gemini failure → existing empty-result fallback; alignment mismatch → trust-subset mapping.

## Data flow

```
words + segments ─► find_candidate_moments ─► ≤25 ranked CandidateMoments
                                                   │
        ffmpeg -ss per candidate @512×288 ─► frames ─► Gemini ─► scored (frame_id-aligned)
                                                   │
                          segment_selection (unchanged) ─► cuts (now sentence-anchored)
```

## Error handling

- `find_candidate_moments`: pure function, wrapped in try/except at the caller; returns `[]` on failure → grid fallback.
- Frame extraction per candidate: if a single `-ss` extraction fails, skip that candidate (don't abort the batch).
- Alignment: never index out of range; map by `frame_id`, drop unmatched.

## Testing

`tests/unit/services/test_candidate_moments.py` (pure, no I/O):
- Sentences with/without numbers score higher when numbers present.
- Silence gaps split units correctly.
- Result count ≤ budget; minimum spacing respected.
- Grid floor appears when transcript is sparse.
- Empty/garbage input → returns `[]` (fallback path).

`visual_analysis` alignment: unit test that mismatched scored/sent counts map by `frame_id` without raising.

## Out of scope (Phase 2+)

- Audio-energy emphasis detection (signal 4).
- Diarization / true speaker-change signal (signal 2).
- The text-only paths: `/api/montaje/generar-pieza` (`_select_segments_llm`) and the orchestrator `select_segments` step — they don't use vision.
