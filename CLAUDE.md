# CLAUDE.md

> Operational context for this project. This file is the source of truth for how we work. Read it fully before starting any task. If you find a contradiction between this file and another source, this file wins.

---

## 1. What this project is

An AI assistant for television content production.

The product takes raw video as input (press conferences, events, archive footage, raw camera material) and produces two outputs:

1. An edited video, with coherent cuts, generated voiceover, lower thirds, music, and the broadcaster's visual template. Ready for human review before air.
2. A complete editorial package: web article, social media summary, alternative angle proposals, and a semantic analysis suggesting related pieces to build a content queue.

Target users are television newsrooms. The commercial goal is to sell the product to roughly one hundred broadcasters, so the architecture is multi-tenant from day one.

In the current phase the media asset manager is mocked. Real integration with AVID (MediaCentral, NEXIS, Interplay) will arrive once the first client is signed. The codebase is structured so that this transition is a matter of swapping an adapter, not rewriting the application.

## 2. Technical philosophy

Five principles that are always respected. If a decision violates any of them, it must be justified in writing under docs/DECISIONS before it is implemented.

**Orchestrated pipelines, not autonomous agents.** Video processing follows a known and repeatable sequence. AI makes bounded decisions inside each step, but the order and the steps themselves are defined by code. This gives predictability, cost control, debuggability, and auditability. Auditability is a legal requirement in media organizations.

**Adapters for everything external.** Anything that may change (LLM provider, MAM, storage, TTS, vector store) lives behind an abstract interface in src/adapters. Business logic never calls third-party SDKs directly.

**Multi-tenant from the first model.** Every table holding client data carries a tenant_id. Every query filters by it. Today there is one tenant, tomorrow there will be one hundred. Retrofitting this is very expensive.

**Traceability by default.** Every AI call, every pipeline decision, every user action is recorded with cost, latency, model, prompt, and result. Without this we cannot audit, optimize, or bill correctly.

**Prompts versioned as code.** Prompts live in files under src/prompts, never embedded in Python strings. They have versions. Changing a prompt is a reviewable commit.

## 3. Stack and versions

When you install or upgrade something, stick to these versions unless explicitly justified.

**Backend**
- Python 3.12
- FastAPI 0.115+
- SQLAlchemy 2.x with async syntax
- Alembic for migrations
- Pydantic 2.x with pydantic-settings
- Celery 5.x with Redis as broker
- uv as the package manager (not pip, not poetry)
- pytest + pytest-asyncio for tests

**Frontend**
- Next.js 15 with the App Router
- TypeScript in strict mode
- Tailwind CSS 4
- shadcn/ui for base components
- Auth.js v5 for authentication
- TanStack Query for server state
- Zustand for client state when needed
- pnpm as the package manager

**Composition service**
- Node.js 20+
- Remotion for declarative video composition
- TypeScript

**Data**
- PostgreSQL 16 with the pgvector extension
- Redis 7 for Celery and cache

**Cloud**
- Vercel for the frontend
- Railway or Fly.io for api and workers
- Cloudflare R2 for file storage
- Neon or Railway Postgres for managed database

**AI**
- Anthropic Claude for reasoning, generation, and vision
- OpenAI Whisper for speech to text
- ElevenLabs for text to speech
- Voyage AI or Cohere for embeddings

## 4. Folder structure

Full monorepo structure. If you need to create a new file, find the place that already exists for it before inventing a new one.

```
asistente-tv/
├── apps/
│   ├── web/                        Next.js frontend
│   ├── api/                        FastAPI backend
│   └── composer/                   Remotion service
├── packages/
│   ├── shared-types/               Shared TS types
│   └── shared-config/              Shared configs
├── infra/                          Docker, Railway, Vercel
├── data/                           Not in git: samples, outputs
├── docs/                           ARCHITECTURE, DECISIONS, DEMO_SCRIPT
├── scripts/                        Operational utilities
└── .github/workflows/              CI and deploys
```

Backend detail:

```
apps/api/src/
├── main.py                         Entry point
├── config.py                       Settings via pydantic-settings
├── routes/                         HTTP endpoints, one per domain
├── core/                           Auth, logging, errors, tenant
├── models/                         SQLAlchemy + Pydantic models
├── db/                             Session, migrations, seeds
├── orchestrator/                   The brain
│   ├── pipeline.py                 Main orchestrator
│   ├── steps/                      One step per file
│   └── state.py                    Pipeline state
├── services/                       Business logic
├── adapters/                       Swappable abstractions
│   ├── llm/                        LLMProvider
│   ├── stt/                        Speech to text
│   ├── tts/                        Text to speech
│   ├── mam/                        Media asset manager
│   ├── storage/                    Files
│   └── vector/                     Vector store
├── workers/                        Celery tasks
└── prompts/                        Versioned prompts in markdown
```

Frontend detail:

```
apps/web/
├── app/
│   ├── (marketing)/                Public landing
│   ├── (app)/                      Protected app
│   │   ├── projects/               Projects list and detail
│   │   └── library/                Pieces library
│   └── api/                        BFF routes if needed
├── components/
│   ├── ui/                         shadcn base
│   ├── upload/                     Video upload
│   ├── pipeline/                   Progress visualization
│   ├── editor/                     Human review
│   └── package/                    Editorial package display
├── lib/                            API client, auth, utils
├── hooks/                          Custom hooks
└── types/                          Shared types
```

## 5. The adapter pattern. The most important decision

Every external dependency lives behind an abstract interface. This is not optional. It is the reason this architecture exists. If you find yourself importing a third-party library outside src/adapters, you are doing it wrong.

### Base interface for an adapter

```python
# src/adapters/llm/base.py
from abc import ABC, abstractmethod
from typing import AsyncIterator

class LLMProvider(ABC):
    """Abstract interface for LLM providers.

    Implementations live alongside this file (claude.py, openai.py).
    Use llm_factory() to obtain an instance based on settings.
    """

    @abstractmethod
    async def generate(
        self,
        system: str,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> "LLMResponse": ...

    @abstractmethod
    async def generate_with_vision(
        self,
        system: str,
        messages: list[dict],
        images: list[bytes],
    ) -> "LLMResponse": ...

    @abstractmethod
    async def stream(
        self,
        system: str,
        messages: list[dict],
    ) -> AsyncIterator[str]: ...

    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float: ...
```

### Concrete implementation

```python
# src/adapters/llm/claude.py
from anthropic import AsyncAnthropic
from .base import LLMProvider, LLMResponse

class ClaudeProvider(LLMProvider):
    def __init__(self, api_key: str, default_model: str):
        self._client = AsyncAnthropic(api_key=api_key)
        self._default_model = default_model

    async def generate(self, system, messages, model=None, temperature=0.7, max_tokens=2000):
        response = await self._client.messages.create(
            model=model or self._default_model,
            system=system,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            text=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            cost=self.estimate_cost(response.usage.input_tokens, response.usage.output_tokens),
        )
    # ... remaining methods
```

### Factory to resolve the implementation

```python
# src/adapters/llm/factory.py
from src.config import settings
from .base import LLMProvider
from .claude import ClaudeProvider
from .openai import OpenAIProvider

def get_llm_provider() -> LLMProvider:
    match settings.llm_provider:
        case "claude":
            return ClaudeProvider(
                api_key=settings.anthropic_api_key,
                default_model=settings.llm_default_model,
            )
        case "openai":
            return OpenAIProvider(...)
        case _:
            raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
```

### How a service consumes it

```python
# src/services/editorial_service.py
class EditorialService:
    def __init__(self, llm: LLMProvider, prompts: PromptStore):
        self._llm = llm
        self._prompts = prompts

    async def generate_web_note(self, transcript: str, context: dict) -> str:
        prompt = self._prompts.render("editorial/web_note", transcript=transcript, **context)
        response = await self._llm.generate(
            system=prompt.system,
            messages=[{"role": "user", "content": prompt.user}],
            temperature=0.3,
        )
        return response.text
```

The service does not know or care which LLM sits underneath. If we switch to OpenAI tomorrow, only a setting changes. If a client requires an open-source model running on their servers, we implement another class and we are done.

### Current adapters

| Adapter | Interface | Current implementations | Future implementations |
| --- | --- | --- | --- |
| LLM | LLMProvider | claude, openai (stub) | gemini, llama local |
| STT | STTProvider | whisper_api, whisper_local | deepgram, assemblyai |
| TTS | TTSProvider | elevenlabs, openai_tts | azure_tts, local voices |
| MAM | MAMAdapter | mock | avid, dalet, octopus |
| Storage | StorageAdapter | r2, local | s3, gcs |
| Vector | VectorAdapter | pgvector | qdrant, weaviate |

## 6. The pipeline. How a video gets processed

The pipeline is an ordered sequence of steps. Each step is a class under src/orchestrator/steps/ that receives a PipelineState and returns a modified PipelineState. Steps do not call each other; they are called by the orchestrator.

### Steps in order

1. **ingest**: downloads the raw video to storage, validates format, extracts technical metadata (duration, resolution, fps, codec).
2. **transcribe**: runs the audio through Whisper. Returns a transcript with per-word and per-segment timestamps. Detects language and performs diarization (who is speaking).
3. **detect_scenes**: uses PySceneDetect to find shot boundaries. Returns a list of scenes with start and end times.
4. **analyze_visual**: for each scene, picks a representative frame and sends it to the vision-capable LLM. Returns a visual description, presence of people, and shot quality (stable, shaky, close-up, wide shot).
5. **select_segments**: the LLM receives the transcript, scenes, and visual analysis. Decides which segments to use for the final piece, in what order, and with what duration. Applies editorial rules (minimum length per soundbite, alternate b-roll, etc).
6. **write_script**: the LLM writes the voiceover script that links the segments. Adapts tone according to the selected template (news, magazine, sports).
7. **generate_voiceover**: the script is sent to the TTS provider. The audio is obtained and stored.
8. **compose_video**: FFmpeg cuts the segments, concatenates them, mixes the voiceover, adds background music with ducking, and delegates to the Remotion service for the lower thirds. Result: the final MP4.
9. **generate_package**: from everything above, the LLM produces the web article, the tweet, the executive summary, the angle proposals, and the semantic analysis for the queue.
10. **index_for_search**: the pieces and their content are indexed in pgvector for future semantic searches.

### Pattern for a pipeline step

```python
# src/orchestrator/steps/transcribe.py
from .base import PipelineStep, PipelineState

class TranscribeStep(PipelineStep):
    name = "transcribe"
    description = "Transcribe audio with timestamps"

    def __init__(self, stt: STTProvider, storage: StorageAdapter):
        self._stt = stt
        self._storage = storage

    async def execute(self, state: PipelineState) -> PipelineState:
        audio_bytes = await self._storage.download(state.video.audio_key)
        transcript = await self._stt.transcribe(
            audio=audio_bytes,
            language=state.config.language,
            with_timestamps=True,
            with_diarization=True,
        )
        state.transcript = transcript
        state.add_event("transcribed", {"segments": len(transcript.segments)})
        return state
```

### Orchestrator pattern

```python
# src/orchestrator/pipeline.py
class Pipeline:
    def __init__(self, steps: list[PipelineStep], state_store: StateStore):
        self._steps = steps
        self._state_store = state_store

    async def run(self, project_id: str) -> PipelineState:
        state = await self._state_store.load(project_id)
        for step in self._steps:
            try:
                await self._state_store.mark_step_running(project_id, step.name)
                state = await step.execute(state)
                await self._state_store.save(project_id, state)
                await self._state_store.mark_step_done(project_id, step.name)
            except Exception as e:
                await self._state_store.mark_step_failed(project_id, step.name, str(e))
                raise
        return state
```

### Rules for adding a new step

1. Create a file under src/orchestrator/steps/.
2. Inherit from PipelineStep, define name and description.
3. Inject dependencies in the constructor (services or adapters).
4. Implement execute() receiving and returning a PipelineState.
5. Register the step in pipeline.py in the correct order.
6. Add a unit test under tests/unit/orchestrator/steps/.

## 7. Coding conventions

### Language rules (read carefully)

The product is sold to Spanish broadcasters, so user-facing text is in Spanish. The code itself, however, is in English. The boundary is strict.

**Always in English**: variable names, function names, class names, file names, folder names, table names, column names, endpoint paths, branch names, commit messages, code comments, docstrings, internal logs, test names, migration descriptions, technical error messages, internal documentation, this file.

**Always in Spanish**: text visible to the end user in the UI, validation messages shown to the journalist, emails sent to clients, demo data that simulates a Spanish newsroom, copy on the marketing landing page. User-facing strings are managed via i18n from day one.

**Prompts to the LLM**: the content sent to the model is written in the language we want the model to answer in (Spanish, in our case). The file metadata, frontmatter, and any comments inside the prompt file are in English.

Example of how this looks in practice:

```python
# src/services/editorial_service.py
class EditorialService:
    """Generates editorial output for a project."""

    async def generate_web_note(self, transcript: str) -> str:
        # Render the Spanish prompt that asks the LLM for a 300-word note
        prompt = self._prompts.render("editorial/web_note", transcript=transcript)
        response = await self._llm.generate(system=prompt.system, messages=[...])
        return response.text  # The text returned is in Spanish, ready for UI
```

### Python

- Type hints everywhere. If there is no type hint, a type hint is missing.
- Pydantic 2 for input and output validation in every route.
- async/await whenever there is I/O. Do not mix sync and async without reason.
- No print statements; use the structured logger configured in core/logging.py.
- Pure functions when possible. Dependencies are injected through the class constructor, not imported inside the function.
- Relative imports inside a package, absolute imports across packages.
- Functions under 50 lines. Files under 300. If you exceed, refactor.

### TypeScript

- Strict mode enabled. No any unless extreme cases justified with a comment.
- Server components by default; client components only when interactivity or hooks are needed.
- Server state via TanStack Query, not useEffect plus fetch.
- Shared types with the backend live in packages/shared-types, generated from the Pydantic models with datamodel-code-generator.
- Small, composable components. If a component exceeds 200 lines, split it.

### Naming

- REST endpoints in plural: /projects, /videos, /segments.
- Table names in plural and snake_case: projects, video_segments.
- Pydantic models in singular and PascalCase: Project, VideoSegment.
- Python files in snake_case. TS/TSX files in kebab-case or PascalCase as Next.js conventions dictate.

### Errors

- Never catch a generic Exception except in the worker outer boundary. Catch the specific exception you expect.
- Domain errors defined in core/errors.py. Each one with its code and message.
- In routes, errors translate to HTTPException with the correct status code.

## 8. Multi-tenancy. How clients are isolated

Every table containing client data carries a tenant_id column. Every query filters by it. Every authenticated request carries the tenant_id in the request context.

### Pattern

```python
# src/core/tenant.py
from contextvars import ContextVar

current_tenant: ContextVar[str] = ContextVar("current_tenant")

def get_current_tenant() -> str:
    try:
        return current_tenant.get()
    except LookupError:
        raise RuntimeError("Tenant context not set")

# src/db/session.py
@asynccontextmanager
async def tenant_session(tenant_id: str):
    """Open a DB session with tenant context set."""
    token = current_tenant.set(tenant_id)
    try:
        async with async_session() as session:
            yield session
    finally:
        current_tenant.reset(token)
```

### Base model

```python
# src/models/base.py
class TenantOwnedMixin:
    tenant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    @classmethod
    def query_for_tenant(cls, session, tenant_id: str):
        return select(cls).where(cls.tenant_id == tenant_id)
```

### Strict rules

- If you add a new table with client data, inherit from TenantOwnedMixin without exceptions.
- Never filter by tenant_id manually ad hoc; use query_for_tenant.
- A specific test per entity proving that one tenant cannot read data from another.

## 9. Prompts. How they are managed

Prompts live in markdown files under src/prompts/, organized by domain. They are loaded at startup and rendered with Jinja2.

### Prompt file structure

```markdown
<!-- src/prompts/editorial/web_note.md -->
---
version: 3
description: Generate a web article from a video transcript
expected_output: markdown
---

## system

Eres un redactor experto en informativos de televisión. Escribes notas claras,
verificadas y con estilo periodístico para la web de la cadena.

## user

A partir de la siguiente transcripción y contexto, redacta una nota de web de
unas 300 palabras siguiendo la línea editorial proporcionada.

Transcripción:
{{ transcript }}

Contexto:
{{ context }}

Línea editorial:
{{ editorial_line }}

Devuelve únicamente el cuerpo de la nota, sin titulares ni metadatos.
```

Note that the prompt body is in Spanish because we want the model to answer in Spanish. The frontmatter (version, description, expected_output) and any comment outside the rendered content are in English.

### How it is loaded and used

```python
# src/services/prompt_store.py
class PromptStore:
    """Loads and renders versioned prompt templates."""

    def __init__(self, base_path: Path):
        self._templates = self._load_all(base_path)

    def render(self, key: str, **vars) -> RenderedPrompt:
        template = self._templates[key]
        return RenderedPrompt(
            system=template.render_section("system", **vars),
            user=template.render_section("user", **vars),
            version=template.metadata["version"],
        )
```

### Rules for prompts

- Each prompt has a version in its frontmatter. Bump the version on any change.
- The system message is stable across calls. The user message holds the dynamic content.
- Always test with at least three examples before bumping the version.
- In the commit message, explain what changed and why.
- Always log the prompt key and the version used in each LLM call.

## 10. Traceability and cost tracking

Every AI call is recorded. This is essential for three things: editorial auditing, cost optimization, and debugging when a result goes wrong.

### Model

```python
# src/models/llm_call.py
class LLMCall(Base, TenantOwnedMixin):
    __tablename__ = "llm_calls"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    pipeline_step: Mapped[str | None]
    provider: Mapped[str]
    model: Mapped[str]
    prompt_key: Mapped[str | None]
    prompt_version: Mapped[int | None]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cost_usd: Mapped[float]
    latency_ms: Mapped[int]
    created_at: Mapped[datetime]
    request_payload: Mapped[dict] = mapped_column(JSONB)  # truncated
    response_payload: Mapped[dict] = mapped_column(JSONB)  # truncated
```

### Decorator that records it

```python
# src/core/observability.py
def track_llm_call(prompt_key: str | None = None):
    """Decorator that records an LLM call after it completes."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.monotonic()
            response = await func(*args, **kwargs)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            await record_llm_call(
                response=response,
                prompt_key=prompt_key,
                latency_ms=elapsed_ms,
            )
            return response
        return wrapper
    return decorator
```

### Rules

- Every service that calls the LLM is decorated.
- An internal dashboard at /admin/costs aggregates by day, tenant, step, and prompt.
- Alerts fire when a project costs more than the configured threshold per tenant.

## 11. Database and migrations

- SQLAlchemy 2 with async syntax.
- Alembic for migrations. Never modify the schema without a migration.
- Reversible migrations whenever possible. Irreversible ones include a comment explaining why.
- One migration per logical change; do not bundle.

### How to create a migration

```bash
cd apps/api
uv run alembic revision --autogenerate -m "add segments duration column"
# review the generated file under db/migrations/versions/
uv run alembic upgrade head
```

### Rules

- Never drop a column without first migrating its data.
- To add a non-null field to a populated table: add it as nullable first, backfill, then change to not null. Three migrations, not one.
- Indexes on every foreign key and on every frequently filtered column.
- tenant_id is always indexed.

## 12. Testing

### Structure

```
tests/
├── unit/                 Pure logic, no I/O
├── integration/          With test DB, with adapter stubs
└── e2e/                  Full pipeline with small fixtures
```

### Rules

- Services and pipeline steps are tested with adapter stubs, never against real APIs.
- Predefined stubs live in tests/fixtures/stubs.py for each adapter.
- E2E tests run against a real test database, brought up with testcontainers.
- Coverage target: 80% in services and orchestrator. Concrete adapters do not need unit coverage; they need integration coverage.
- A tenant-isolation test for every TenantOwned entity.

### How to run tests

```bash
cd apps/api
uv run pytest                       # everything
uv run pytest tests/unit            # fast
uv run pytest -k "tenant"           # filtered
uv run pytest --cov=src             # with coverage
```

## 13. Local development. How to run the project

### First time setup

```bash
# 1. Infrastructure services
docker compose up -d                # postgres + redis

# 2. Backend
cd apps/api
uv sync                             # install dependencies
uv run alembic upgrade head         # migrations
uv run python scripts/seed_demo_data.py   # demo data
uv run uvicorn src.main:app --reload --port 8000

# 3. Worker (in another terminal)
cd apps/api
uv run celery -A src.workers.celery_app worker --loglevel=info

# 4. Frontend
cd apps/web
pnpm install
pnpm dev                            # starts on :3000

# 5. Composer
cd apps/composer
pnpm install
pnpm dev                            # starts on :4000
```

### Environment variables

A .env.example file lives under infra/. Copy it to .env in each app that needs it. Key variables:

- ANTHROPIC_API_KEY
- OPENAI_API_KEY (for Whisper API if used)
- ELEVENLABS_API_KEY
- DATABASE_URL
- REDIS_URL
- R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
- LLM_PROVIDER (claude / openai)
- MAM_PROVIDER (mock / avid)
- STORAGE_PROVIDER (r2 / local)

## 14. Deployment

**Frontend**: pushing to main triggers an automatic Vercel deploy.

**Backend and workers**: Railway with Dockerfile. Push to main triggers build and deploy. Health check at /healthz.

**Database**: managed Postgres on Railway or Neon. Migrations run manually with one command before deploying the new backend, never automatically.

**Storage**: Cloudflare R2 with two buckets, one for staging and one for production.

**Monitoring**: Sentry for errors. Axiom for logs. Grafana Cloud for metrics if needed.

**Secrets**: stored in Railway and Vercel. Never in the repo. A CI check fails if it detects an API key in code.

## 15. Recipes for common tasks

### Add a new LLM provider

1. Create src/adapters/llm/new_provider.py inheriting from LLMProvider.
2. Implement all abstract methods.
3. Add it to the match in factory.py.
4. Add the new value to the LLM_PROVIDER setting.
5. Integration test under tests/integration/adapters/llm/.

### Add a new pipeline step

1. Create src/orchestrator/steps/new_step.py inheriting from PipelineStep.
2. Inject the required dependencies.
3. Implement execute() returning the updated state.
4. Register the step in pipeline.py at the correct position.
5. Unit test with stubs under tests/unit/orchestrator/steps/.
6. Update the step list in section 6 of this file.

### Add a new prompt

1. Create src/prompts/domain/name.md with frontmatter (version, description).
2. Test with three different inputs in a small script.
3. Use it from a service via prompts.render("domain/name", **vars).

### Add a new domain entity

1. SQLAlchemy model in src/models/entity.py inheriting from Base and TenantOwnedMixin.
2. Corresponding Pydantic schemas for input and output.
3. Alembic migration.
4. Repository in src/db/repositories/entity_repository.py.
5. Service in src/services/entity_service.py.
6. Routes in src/routes/entity.py.
7. Tests: service unit, routes integration, tenant isolation.

### Switch the AI model used in a specific step

Do not touch the step code. Change the model in the service configuration or in the setting. If a different model is needed in the same step for different clients, add a per-tenant configuration layer.

## 16. Anti-patterns. What never happens

- Importing anthropic, openai, elevenlabs, or any third-party SDK outside src/adapters. If you need it there, the design is wrong.
- Embedding prompts as string literals in service code. They live in files.
- Forgetting tenant_id in a new table or in a query.
- Calling another pipeline step from inside a step. If two things need to happen in order, they are two steps.
- Using print, time.sleep, or synchronous requests.
- Broad try/except that swallows errors. Catch the specific exception and log it.
- Committing a .env, secrets, or files from data/.
- Modifying tests so they pass without understanding the change. If a test fails, understand why.
- Creating a new top-level module without justification. The structure is intentional.
- Running the pipeline synchronously from an HTTP endpoint. Always via Celery.

## 17. Domain glossary

Terms you will see repeatedly. Knowing them prevents misunderstandings. Many of these stay in Spanish even in English documentation because they are industry terms.

**MAM (Media Asset Management)**: system that stores and catalogs video and metadata. AVID, Dalet, and Cinegy are MAMs.

**AVID**: dominant software ecosystem in television production. Composed of several pieces; the most relevant are MediaCentral (application layer), NEXIS (storage), Interplay or Production Management (asset management), Media Composer (editing), and iNews (newsroom).

**NEXIS**: AVID's shared storage where the videos live.

**MediaCentral**: the layer that organizes what is in NEXIS and lets external tools query it via API.

**iNews**: AVID's newsroom and rundown system. Where the anchor's text is written.

**Escaleta (rundown)**: the order of pieces in a program. The roadmap of the broadcast.

**Pieza (piece)**: a content unit in a program. A news item, a report, an interview.

**Cola (queue)**: an editorially proposed sequence of related pieces.

**Cesta (basket)**: in the product context, the set of resources a journalist gathers to build a piece.

**Brutos (raw footage)**: unedited recorded material.

**Plano (shot)**: a continuous camera take. Changes when there is a cut to another take.

**Plano de recurso (b-roll)**: footage interleaved with soundbites for visual variety.

**Voz en off (voiceover)**: the journalist or anchor narrating over the images.

**Lower third**: the on-screen graphic at the bottom of the frame showing the speaker's name.

**Ducking**: mixing technique that automatically lowers the music when there is voice.

**Playout**: the system that actually broadcasts the signal on air.

**Ingesta (ingest)**: the process of bringing new material into the central system.

**Plantilla (template)**: the set of visual elements (typography, colors, intros, lower thirds) that give a broadcaster its identity.

**PoC**: Proof of Concept.

**Multi-tenant SaaS**: cloud product serving multiple clients from the same infrastructure, isolating their data logically.

## 18. How to work with me (Claude) on this project

When you ask me to do something, you will get better results if you:

- Give me one bounded task per session, not several at once.
- Tell me the specific file to modify, not just "the backend".
- Indicate whether tests are needed and of what kind.
- Tell me whether the task is exploratory (propose options) or executive (follow the established pattern).
- Point out which sections of this CLAUDE.md apply to the task.

I will:

- Read this file before starting.
- Look for similar patterns already implemented before inventing a new one.
- Warn you if a task violates the project principles instead of doing it silently.
- Write tests for what I add unless told otherwise.
- Update this CLAUDE.md when a task changes something important about the architecture.

When I finish something significant, I will update section 19 with the current state.

## 19. Current project state

> Keep this section alive. Update it when you finish a sprint or a meaningful feature.

**Current sprint**: Sprint 1, foundations.

**Completed**:

- [ ] Folder structure created
- [ ] Monorepo with pnpm workspaces
- [ ] apps/web initialized with Next.js 15
- [ ] apps/api initialized with FastAPI and uv
- [ ] apps/composer initialized with Remotion
- [ ] docker-compose for postgres and redis
- [ ] Initial models and first migration
- [ ] Celery configured with a sample task
- [ ] Auth.js on the frontend with simple login
- [ ] Deployment on Vercel and Railway

**In progress**: nothing yet.

**Next**: Sprint 2, adapters and interfaces.

**Recent decisions** (linked under docs/DECISIONS):

- 001-tech-stack.md
- 002-adapter-pattern.md
- 003-multitenant-from-day-one.md

## 20. When to ask before acting

If you find yourself in any of these situations, stop and ask before implementing:

- The task requires changing an adapter that is in use across all clients.
- The task involves modifying a prompt that is already in production.
- The task requires touching the tenant logic.
- The task suggests adding a new third-party SDK.
- The user asks for something that violates an anti-pattern in section 16.
- The task affects more than three files across different modules.

In those cases, propose a plan instead of executing. Two minutes spent reviewing a plan beats half an hour reviewing a misdirected change.