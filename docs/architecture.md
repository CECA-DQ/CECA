# Architecture

How the system is laid out and why the core structural decisions were made. For the adapter pattern see `adapters.md`; for the pipeline see `pipeline.md`; for tenant isolation see `multitenancy.md`.

## Stack and versions

Stick to these versions unless a deviation is justified in an ADR.

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Web framework | FastAPI 0.115+ |
| ORM | SQLAlchemy 2.x (async syntax) |
| Migrations | Alembic |
| Validation | Pydantic 2.x + pydantic-settings |
| Task queue | Celery 5.x |
| Broker / cache | Redis 7 |
| Database | PostgreSQL 16 + pgvector extension |
| Package manager | uv (not pip, not poetry) |
| Tests | pytest + pytest-asyncio |
| LLM | Groq (`llama-3.3-70b-versatile`, default) · Anthropic Claude · Gemini · OpenAI (stub) |
| STT | Groq Whisper (default) · OpenAI Whisper API · mock |
| TTS | ElevenLabs (default) · mock |
| Storage | Local filesystem (default) · Cloudflare R2 |
| Vector | mock (default) · pgvector · pgfts |

Cloud targets: Railway or Fly.io for api/workers, Cloudflare R2 for files, Neon or Railway Postgres for the managed DB. Settings are defined in `apps/api/src/config.py` (`Settings`), loaded from `apps/api/.env`. Env template: `infra/.env.example`.

## Folder structure

The frontend lives in a separate repository. Find the existing place for a file before inventing a new location.

```
CECA/
├── apps/api/                         FastAPI backend
│   ├── src/
│   │   ├── main.py                   App entry point; registers all routers
│   │   ├── config.py                 Settings via pydantic-settings
│   │   ├── routes/                   HTTP endpoints, one module per domain
│   │   ├── core/                     auth, logging, errors, tenant, observability
│   │   ├── models/                   SQLAlchemy models
│   │   ├── db/                       session.py + Alembic migrations
│   │   ├── services/                 Business logic (prompt_store, segment_selection,
│   │   │                             visual_analysis, narrative_timeline)
│   │   ├── adapters/                 llm · stt · tts · mam · storage · vector
│   │   ├── orchestrator/             pipeline.py, state.py, steps/
│   │   ├── prompts/                  Versioned prompts (markdown)
│   │   └── workers/                  Celery app + pipeline tasks
│   ├── tests/                        unit / integration / e2e / fixtures
│   ├── pyproject.toml
│   └── alembic.ini
├── infra/                            docker-compose, .env.example, deploy configs
├── docs/                             this directory + DECISIONS/ (ADRs)
├── scripts/                          operational utilities
└── data/                             not in git: videos, seeds, storage
```

## Request flow

```
HTTP request → FastAPI endpoint → Celery task (Redis broker) → Pipeline.run()
  → step 1 … step 11 (DB updated at every step boundary) → EditorialPackage saved
```

The HTTP endpoint returns immediately with a task id; the worker runs the pipeline and persists state as it goes. Progress is polled via the status endpoint.

## Design decisions

**Why a pipeline and not autonomous agents.** Autonomous agents are non-deterministic and hard to audit. In television every editorial decision must be traceable: which model decided what, on what input, at what cost, when. The pipeline enforces this by design — the full execution trace is stored in `pipeline_step_runs.result` (JSONB) for every run.

**Why JSONB for step results.** Each of the 11 steps produces a structurally different output. Normalizing each into its own table would mean 11 extra tables with complex joins and no flexibility. JSONB stores each step's full result, stays queryable in PostgreSQL, and lets schemas evolve without new migrations.

**Why Celery for the pipeline.** Video processing takes 15–90 seconds. Running it inside an HTTP request would block the connection, prevent retries, and make progress polling impossible. Celery decouples the trigger (HTTP) from the execution (worker) and enables retries with backoff.

**Why the worker calls `engine.dispose()` at task start.** SQLAlchemy's async engine keeps a connection pool tied to the event loop that created it. Celery calls `asyncio.run()`, creating a new loop per task; without disposing, asyncpg raises "another operation is in progress" because the pool hands out connections bound to the old loop. Disposing forces fresh connections.

**Why `X-Tenant-ID` header instead of JWT.** This is a demo. The header makes API testing trivial with `curl`. `core/auth.py` is the single place to swap in real JWT validation — nothing else changes. See `multitenancy.md`.
