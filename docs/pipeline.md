# The Pipeline

The pipeline is a fixed, ordered sequence of steps. Each step is a class under `src/orchestrator/steps/` that receives a `PipelineState` and returns it modified. Steps never call each other — the orchestrator (`src/orchestrator/pipeline.py`) calls them in order.

## The 11 steps

Source of truth: `PIPELINE_STEP_NAMES` and `build_pipeline()` in `src/orchestrator/pipeline.py`. Each `name`/`description` is defined on the step class.

| # | Step | What it does |
|---|---|---|
| 1 | `ingest` | Ingest raw video and extract technical metadata (duration, resolution, fps, codec) |
| 2 | `transcribe` | Audio → text with per-segment timestamps via Whisper |
| 3 | `detect_scenes` | Group the transcript into logical scenes for downstream analysis |
| 4 | `analyze_visual` | Infer per-scene visual context (vision-first: frames scored, see `services/visual_analysis.py`) |
| 5 | `select_segments` | Select and order key segments via LLM editorial judgment |
| 6 | `write_script` | Write the voiceover script linking the selected segments — **guardrails** |
| 7 | `generate_voiceover` | Script → audio via TTS |
| 8 | `compose_video` | Assemble the final MP4 from selected clips via FFmpeg (voiceover mix, music ducking, lower thirds) |
| 9 | `generate_package` | Generate web article, tweet, executive summary, angle proposals — **guardrails** |
| 10 | `generate_tv_pieces` | Generate presenter lead, soundbites, VTR script, graphics, and rundown |
| 11 | `index_for_search` | Index content in the vector store for semantic search |

## PipelineState

`src/orchestrator/state.py`. A dataclass that flows through every step. Each step reads what it needs and writes its output back. Typed fields hold the working data (`transcript`, `scenes`, `visual_analysis`, `selected_segments`, `voiceover_script`, `voiceover_audio_key`, `composed_video_key`, `editorial_package`, `tv_pieces`); `step_results: dict[str, dict]` holds each step's raw result and is persisted to `PipelineStepRun.result` (JSONB) for traceability.

## What the orchestrator does

`Pipeline.run(project_id, tenant_id, pipeline_run_id=None)`:
- Creates a `PipelineRun` and one `PipelineStepRun` per step (status `pending`), or marks a pre-created run as `running`.
- For each step: `pending → running`, `execute()`, then `completed` (persisting `state.step_results[step.name]`) or `failed` (persisting the error) and re-raise.
- On success: marks the run `completed` and upserts the `EditorialPackage`.
- On failure: marks the run `failed` and re-raises.

Every step's result is stored permanently, so you can audit exactly what each AI call produced, on what input, and when. All DB access goes through `tenant_session(tenant_id)`.

## Guardrails (LLM steps)

Both LLM-generating steps validate output before accepting it:

- **`write_script`** — voiceover text: not empty, 30–300 words.
- **`generate_package`** — JSON package: valid JSON object (strips markdown fences), all four keys present (`web_article`, `tweet`, `executive_summary`, `angle_proposals`), `angle_proposals` is a list, `tweet` ≤ 280 chars.

Retry strategy: (1) normal prompt; (2) on validation failure, one retry appending the bad response + a correction instruction at lower temperature; (3) on second failure, fall back to mock data, log a warning, and let the pipeline continue. A temporary LLM quality issue never crashes the pipeline.

## Adding a new step

1. Create `src/orchestrator/steps/<my_step>.py` inheriting from `PipelineStep` (`steps/base.py`).
2. Set the `name` and `description` class attributes.
3. Implement `async def execute(self, state: PipelineState) -> PipelineState`.
4. Write the step's result into `state.step_results[self.name]` (and any typed `state` fields it produces).
5. Inject dependencies (services/adapters) via the constructor.
6. Register the step: add its name to `PIPELINE_STEP_NAMES` and instantiate it in `build_pipeline()`, both in `pipeline.py`, at the correct position.
7. Add a unit test under `tests/unit/orchestrator/steps/` using adapter stubs.
8. Update the step table above.
