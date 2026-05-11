# CECA — AI Assistant for Television Content Production

CECA is a backend API that takes raw video as input (press conferences, events, archive footage) and produces two outputs:

1. **An edited video** — coherent cuts, generated voiceover, lower thirds, background music with ducking, and the broadcaster's visual template. Ready for human review before air.
2. **A complete editorial package** — web article, social media summary, alternative angle proposals, and a semantic analysis for content queue building.

Target users are Spanish television newsrooms. The architecture is multi-tenant from day one, designed to serve ~100 broadcasters from shared infrastructure.

---

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Local Development](#local-development)
- [API Reference](#api-reference)
- [The Pipeline](#the-pipeline)
- [Adapter Pattern](#adapter-pattern)
- [Prompts and Guardrails](#prompts-and-guardrails)
- [Multi-tenancy](#multi-tenancy)
- [Key Design Decisions](#key-design-decisions)

---

## Architecture

The system is built around two core principles:

### Orchestrated Pipeline, Not Autonomous Agents

Video processing follows a fixed, code-defined sequence of 10 steps. AI makes bounded decisions *inside* each step, but the order and structure are always deterministic. This is not optional — auditability is a legal requirement in media organizations.

```
HTTP Request
    │
    ▼
FastAPI endpoint  ──► Celery task (via Redis broker)
                            │
                            ▼
                       Pipeline.run()
                            │
                ┌───────────┼───────────┐
                ▼           ▼           ▼
           Step 1...    Step N...   Step 10
         (updates DB at each step boundary)
                            │
                            ▼
                    EditorialPackage saved to DB
```

### Adapters for Everything External

Every third-party dependency lives behind an abstract interface. Business logic never imports SDKs directly. Swapping a provider (e.g., ElevenLabs → Azure TTS) means changing one setting, not touching application code.

```
Service / Step
    │
    ▼
Abstract Interface (e.g., LLMProvider)
    │
    ├── ClaudeProvider
    ├── GroqProvider        ← current default
    └── OpenAIProvider (stub)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Web framework | FastAPI 0.115+ |
| ORM | SQLAlchemy 2.x (async) |
| Migrations | Alembic |
| Validation | Pydantic 2.x + pydantic-settings |
| Task queue | Celery 5.x |
| Broker / cache | Redis 7 |
| Database | PostgreSQL 16 + pgvector extension |
| Package manager | uv |
| LLM | Groq (llama-3.3-70b) / Anthropic Claude |
| STT | OpenAI Whisper |
| TTS | ElevenLabs |
| Storage | Local filesystem / Cloudflare R2 |
| Vector store | pgvector / mock |

---

## Project Structure

```
CECA/
├── apps/
│   └── api/
│       ├── src/
│       │   ├── main.py                 FastAPI app entry point
│       │   ├── config.py               Settings via pydantic-settings (.env)
│       │   ├── core/
│       │   │   ├── auth.py             Tenant identity from X-Tenant-ID header
│       │   │   ├── errors.py           Domain error definitions
│       │   │   ├── logging.py          Structured logging setup
│       │   │   ├── observability.py    LLM call tracking decorator
│       │   │   └── tenant.py           ContextVar for current tenant
│       │   ├── db/
│       │   │   ├── session.py          Async SQLAlchemy engine + tenant_session()
│       │   │   └── migrations/         Alembic migrations
│       │   │       └── versions/
│       │   │           ├── 0001_create_tenants_and_projects.py
│       │   │           └── 0002_add_video_pipeline_editorial.py
│       │   ├── models/
│       │   │   ├── base.py             Base + TenantOwnedMixin
│       │   │   ├── tenant.py           Tenant model
│       │   │   ├── project.py          Project model
│       │   │   ├── video.py            Video asset model
│       │   │   ├── pipeline_run.py     PipelineRun + PipelineStepRun + RunStatus
│       │   │   └── editorial_package.py EditorialPackage model
│       │   ├── routes/
│       │   │   └── projects.py         All project + pipeline endpoints
│       │   ├── services/
│       │   │   └── prompt_store.py     Loads and renders versioned prompt templates
│       │   ├── adapters/
│       │   │   ├── llm/                LLMProvider (claude, groq, openai stub)
│       │   │   ├── stt/                STTProvider (whisper, mock)
│       │   │   ├── tts/                TTSProvider (elevenlabs, mock)
│       │   │   ├── mam/                MAMAdapter (mock, avid stub)
│       │   │   ├── storage/            StorageAdapter (local, r2 stub)
│       │   │   └── vector/             VectorAdapter (pgvector, mock)
│       │   ├── orchestrator/
│       │   │   ├── state.py            PipelineState dataclass
│       │   │   ├── pipeline.py         Orchestrator + DB state management
│       │   │   └── steps/
│       │   │       ├── base.py         PipelineStep ABC
│       │   │       ├── ingest.py       Step 1 — video metadata extraction
│       │   │       ├── transcribe.py   Step 2 — audio → text with timestamps
│       │   │       ├── detect_scenes.py Step 3 — shot boundary detection
│       │   │       ├── analyze_visual.py Step 4 — frame-level visual description
│       │   │       ├── select_segments.py Step 5 — editorial segment selection
│       │   │       ├── write_script.py  Step 6 — LLM voiceover script + guardrails
│       │   │       ├── generate_voiceover.py Step 7 — TTS audio generation
│       │   │       ├── compose_video.py Step 8 — FFmpeg + Remotion assembly
│       │   │       ├── generate_package.py Step 9 — LLM editorial package + guardrails
│       │   │       └── index_for_search.py Step 10 — vector store indexing
│       │   ├── prompts/
│       │   │   └── pipeline/
│       │   │       ├── write_script.md     Versioned voiceover prompt (v1)
│       │   │       └── generate_package.md Versioned editorial package prompt (v1)
│       │   └── workers/
│       │       ├── celery_app.py       Celery app configuration
│       │       └── pipeline_tasks.py   process_project Celery task
│       ├── tests/
│       │   └── unit/adapters/          Adapter unit tests
│       ├── pyproject.toml
│       └── alembic.ini
├── infra/
│   ├── docker-compose.yml              PostgreSQL 16 + Redis 7
│   └── .env.example
└── docs/
    └── DECISIONS/                      Architecture decision records
```

---

## Local Development

### Prerequisites

- Docker Desktop running
- Python 3.12
- `uv` package manager (`pip install uv`)

### 1. Start infrastructure

```bash
docker compose -f infra/docker-compose.yml up -d
# PostgreSQL on port 5433, Redis on port 6379
```

### 2. Install dependencies

```bash
cd apps/api
uv sync
```

### 3. Configure environment

Copy and edit the env file:

```bash
cp infra/.env.example apps/api/.env
```

Minimum required variables:

```bash
DATABASE_URL=postgresql+asyncpg://ceca:ceca@localhost:5433/ceca
REDIS_URL=redis://localhost:6379/0
GROQ_API_KEY=your_key_here
LLM_PROVIDER=groq
MAM_PROVIDER=mock
STORAGE_PROVIDER=local
VECTOR_PROVIDER=mock
```

### 4. Run migrations

```bash
cd apps/api
uv run alembic upgrade head
```

### 5. Start the API server

```bash
# Terminal 1
uv run uvicorn src.main:app --reload --port 8000
```

### 6. Start the Celery worker

```bash
# Terminal 2
uv run celery -A src.workers.celery_app worker --loglevel=info
```

### 7. Verify everything is up

```bash
curl http://127.0.0.1:8000/healthz
# → {"status": "ok"}
```

> **Note:** Use `127.0.0.1` instead of `localhost`. On macOS, Docker may occupy port 8000 on IPv6, causing `localhost` to resolve to the wrong process.

### Running tests

```bash
cd apps/api
uv run pytest              # all
uv run pytest tests/unit   # fast, no DB required
uv run pytest -k "tenant"  # filtered by name
uv run pytest --cov=src    # with coverage report
```

---

## API Reference

All endpoints require the `X-Tenant-ID` header to identify the broadcaster.

### Projects

| Method | Path | Status | Description |
|---|---|---|---|
| `POST` | `/projects` | 201 | Create a new project |
| `GET` | `/projects` | 200 | List all projects for the tenant |
| `GET` | `/projects/{id}` | 200 | Get a single project |
| `POST` | `/projects/{id}/videos` | 201 | Register a video asset for a project |

### Pipeline

| Method | Path | Status | Description |
|---|---|---|---|
| `POST` | `/projects/{id}/pipeline/run` | 202 | Trigger processing (async, via Celery) |
| `GET` | `/projects/{id}/pipeline/status` | 200 | Step-by-step pipeline status |
| `GET` | `/projects/{id}/results` | 200 | Completed editorial package |

### Ops

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Health check |

### Example: Full flow

```bash
# 1. Create project
curl -s -X POST http://127.0.0.1:8000/projects \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: antena3" \
  -d '{"title": "Rueda de prensa plan ferroviario"}'

# 2. Register video (use the id returned above)
curl -s -X POST http://127.0.0.1:8000/projects/{id}/videos \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: antena3" \
  -d '{"original_filename": "rueda_prensa.mp4"}'

# 3. Trigger pipeline → returns task_id immediately
curl -s -X POST http://127.0.0.1:8000/projects/{id}/pipeline/run \
  -H "X-Tenant-ID: antena3"

# 4. Poll status (pipeline takes ~15-30s)
curl -s http://127.0.0.1:8000/projects/{id}/pipeline/status \
  -H "X-Tenant-ID: antena3"

# 5. Get results when status is "completed"
curl -s http://127.0.0.1:8000/projects/{id}/results \
  -H "X-Tenant-ID: antena3"
```

### Pipeline status response shape

```json
{
  "run_id": "uuid",
  "status": "completed",
  "started_at": "2026-05-11T10:00:00Z",
  "finished_at": "2026-05-11T10:00:17Z",
  "steps": [
    {
      "step_name": "ingest",
      "position": 0,
      "status": "completed",
      "started_at": "...",
      "finished_at": "...",
      "result": { "duration_seconds": 187.4, "resolution": "1920x1080", ... }
    },
    ...
  ]
}
```

### Results response shape

```json
{
  "id": "uuid",
  "project_id": "uuid",
  "web_article": "## Título\n\nCuerpo en markdown...",
  "tweet": "🚄 Texto del tweet #Hashtag",
  "executive_summary": "2-3 frases para el editor.",
  "angle_proposals": ["Ángulo 1", "Ángulo 2", ...],
  "voiceover_script": "Texto completo de la voz en off...",
  "voiceover_audio_key": "audio/{project_id}/voiceover.mp3",
  "composed_video_key": "output/{project_id}/final.mp4",
  "created_at": "..."
}
```

---

## The Pipeline

The pipeline is a fixed sequence of 10 steps. Each step is a class that receives a `PipelineState` and returns it modified. Steps never call each other — the orchestrator calls them in order.

```
PipelineState flows through:

 1. ingest           → video metadata (duration, resolution, codec, fps)
 2. transcribe       → full transcript with per-segment timestamps + speaker diarization
 3. detect_scenes    → list of scenes with start/end times and shot types
 4. analyze_visual   → per-scene description, has_people, shot quality
 5. select_segments  → editorial selection: which scenes, in what order, with justification
 6. write_script     → LLM generates voiceover text linking selected segments  ◄ guardrails
 7. generate_voiceover → TTS converts script to audio file
 8. compose_video    → FFmpeg cuts + voiceover mix + music ducking + lower thirds
 9. generate_package → LLM generates web article, tweet, summary, angles         ◄ guardrails
10. index_for_search → content indexed in vector store for semantic search
```

The `Pipeline` class manages all DB transitions:
- Creates a `PipelineRun` record before execution starts
- Creates all 10 `PipelineStepRun` records with status `pending`
- Transitions each step: `pending → running → completed / failed`
- Stores each step's full output as JSONB in `PipelineStepRun.result`
- On success: upserts the `EditorialPackage` record
- On failure: marks the run and the failing step as `failed`, re-raises the exception

Each step's result is permanently stored, so you can audit exactly what each AI call produced, what data it received, and when.

### Adding a new step

1. Create `src/orchestrator/steps/my_step.py` inheriting from `PipelineStep`
2. Set `name` and `description` class attributes
3. Implement `async def execute(self, state: PipelineState) -> PipelineState`
4. Write the result into `state.step_results[self.name]`
5. Add the step name to `PIPELINE_STEP_NAMES` in `pipeline.py`
6. Instantiate and add it to `build_pipeline()` in `pipeline.py`
7. Write a unit test under `tests/unit/orchestrator/steps/`

---

## Adapter Pattern

Every external dependency lives behind an abstract interface in `src/adapters/`. This is the single most important architectural rule in the codebase.

```python
# Wrong — never import SDKs directly in services or steps:
from anthropic import AsyncAnthropic

# Correct — always use the adapter:
from src.adapters.llm.factory import get_llm_provider
llm = get_llm_provider()
response = await llm.generate(system=..., messages=...)
```

### Current adapters

| Adapter | Interface | Active | Stub / Planned |
|---|---|---|---|
| LLM | `LLMProvider` | `groq`, `claude` | `openai` stub |
| STT | `STTProvider` | `mock` | `whisper_api`, `whisper_local` |
| TTS | `TTSProvider` | `mock` | `elevenlabs` |
| MAM | `MAMAdapter` | `mock` | `avid` stub |
| Storage | `StorageAdapter` | `local` | `r2` stub |
| Vector | `VectorAdapter` | `mock` | `pgvector` |

### Switching a provider

Change one line in `.env` — no code changes required:

```bash
LLM_PROVIDER=groq         # → GroqProvider
LLM_PROVIDER=claude       # → ClaudeProvider
STORAGE_PROVIDER=r2       # → R2StorageAdapter
```

### Adding a new provider

1. Create `src/adapters/{type}/my_provider.py` implementing the abstract base class
2. Add a `case` to the factory in `src/adapters/{type}/factory.py`
3. Add the new value to the relevant setting in `config.py`

---

## Prompts and Guardrails

Prompts are versioned markdown files under `src/prompts/`, never string literals in code.

### File format

```markdown
---
version: 1
description: What this prompt does
expected_output: plain_text | json
---

## system

Stable system message (same across all calls).

## user

Dynamic user message with {{ jinja2_variables }}.
```

### PromptStore

```python
from src.services.prompt_store import get_prompt_store

store = get_prompt_store()
prompt = store.render("pipeline/write_script", transcript="...", segments="...")

response = await llm.generate(
    system=prompt.system,
    messages=[{"role": "user", "content": prompt.user}]
)
```

The `PromptStore` is a module-level singleton. It discovers and loads all `.md` files under `src/prompts/` automatically at first use. Templates are rendered with Jinja2 using `StrictUndefined` — missing variables raise an error immediately.

### Guardrails

Both LLM steps validate their output before accepting it:

**`write_script`** validates the voiceover text:
- Not empty
- Between 30 and 300 words

**`generate_package`** validates the JSON package:
- Valid JSON object (strips markdown code fences if present)
- All 4 keys present: `web_article`, `tweet`, `executive_summary`, `angle_proposals`
- `angle_proposals` is a list
- `tweet` ≤ 280 characters

**Retry strategy (both steps):**
1. First call with the normal prompt
2. On validation failure — one retry, appending the bad response + a correction instruction to the conversation (lower temperature)
3. On second failure — fall back to mock data, log a warning, pipeline continues

A temporary LLM quality issue never crashes the pipeline.

### Updating a prompt

1. Edit the `.md` file
2. Bump the `version` in the frontmatter
3. Test with at least 3 different inputs
4. In the commit message, explain what changed and why

---

## Multi-tenancy

Every table holding client data has a `tenant_id` column. Every query filters by it. This was a day-one decision — retrofitting multi-tenancy later is prohibitively expensive.

### Base mixin

```python
class TenantOwnedMixin:
    tenant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    @classmethod
    def query_for_tenant(cls, tenant_id: str):
        return select(cls).where(cls.tenant_id == tenant_id)
```

Every model with client data must inherit from this. No exceptions.

### In routes

Tenant identity comes from the `X-Tenant-ID` request header (demo). In production this will be extracted from a JWT.

```python
async def get_tenant_id(x_tenant_id: str = Header(...)) -> str:
    return x_tenant_id.strip()
```

### Rules

- New table with client data → must inherit `TenantOwnedMixin`
- Never filter by `tenant_id` inline — always use `Model.query_for_tenant(tenant_id)`
- Write a tenant-isolation test for every new entity (one tenant must not read another's data)

---

## Key Design Decisions

### Why pipeline and not autonomous agents?

Autonomous agents (where AI decides what to call next) are non-deterministic and hard to audit. In television, every editorial decision must be traceable: what model decided what, based on what input, at what cost, at what moment. The pipeline enforces this by design — the full execution trace is stored in `pipeline_step_runs.result` for every run.

### Why JSONB for step results?

Each of the 10 steps produces a structurally different output. Normalizing each into its own table would create 10 extra tables with complex joins and no flexibility. JSONB stores each step's full result, is queryable in PostgreSQL, and allows schemas to evolve without new migrations.

### Why Celery for the pipeline?

Video processing takes 15–90 seconds. Running this synchronously inside an HTTP request would block the connection, prevent retries, and make progress polling impossible. Celery decouples the trigger (HTTP) from the execution (worker), enables retries with exponential backoff, and the broker makes the pipeline observable.

### Why does the worker call `engine.dispose()` at the start?

SQLAlchemy's async engine maintains a connection pool tied to its creation event loop. Celery workers call `asyncio.run()` which creates a new event loop per task. Without `engine.dispose()`, asyncpg raises `cannot perform operation: another operation is in progress` because the pool hands out connections bound to the old loop. Disposing forces fresh connections in the new loop.

### Why `X-Tenant-ID` header instead of JWT?

This is a demo. The header makes API testing trivial with `curl` and no token management. `core/auth.py` is the single place to swap in real JWT validation — nothing else in the codebase needs to change.
