# CLAUDE.md

> Operational index for this project. This file is loaded into context every session, so it stays lean.
> Detailed guidance lives in `docs/` and is read on demand. **Read the relevant `docs/` file before working in that area** (see the map at the bottom). If this file and another source conflict, this file wins.

---

## What this project is

An AI assistant for television content production, sold to Spanish broadcasters.
Input: raw video (press conferences, events, archive footage).
Output: (1) an edited video — cuts, voiceover, lower thirds, music, the broadcaster's template, ready for human review; and (2) an editorial package — web article, social summary, alternative angles, and a semantic analysis for the content queue.

Multi-tenant from day one (~100 broadcasters on shared infra). The MAM is mocked today; real AVID integration arrives once the first client signs — a matter of swapping an adapter. The frontend lives in a separate repository and talks to this API over HTTP.

## Five principles (never violated without a written ADR under `docs/DECISIONS/`)

1. **Orchestrated pipelines, not autonomous agents.** Order and steps are defined by code; AI makes bounded decisions inside each step. Gives predictability, cost control, auditability (a legal requirement in media).
2. **Adapters for everything external.** LLM, MAM, storage, TTS, STT, vector — all behind an abstract interface in `src/adapters`. Business logic never calls a third-party SDK directly.
3. **Multi-tenant from the first model.** Every table with client data carries `tenant_id`; every query filters by it.
4. **Traceability by default.** Every AI call and pipeline decision is recorded with cost, latency, model, prompt, and result.
5. **Prompts versioned as code.** Prompts live in files under `src/prompts`, never as Python string literals. They have versions; changing one is a reviewable commit.

## Hard rules and anti-patterns (these never happen)

- Never import `anthropic`, `openai`, `groq`, `google.genai`, `elevenlabs`, or any third-party SDK outside `src/adapters/`. If you need it elsewhere, the design is wrong.
- Never embed a prompt as a string literal in service/step code. Prompts are files in `src/prompts/`.
- Never add a table or write a query without `tenant_id`. New client-data tables inherit `TenantOwnedMixin`; never filter by `tenant_id` ad hoc — use `Model.query_for_tenant(tenant_id)`.
- Never call one pipeline step from inside another. If two things must happen in order, they are two steps.
- Never run the pipeline synchronously from an HTTP endpoint. Always via Celery.
- Never use `print`, `time.sleep`, or synchronous `requests`. Use the structured logger and `async`/`await`.
- Never catch a bare `Exception` except at the worker's outer boundary. Catch the specific exception and log it.
- Never commit `.env`, secrets, or anything under `data/`.
- Never modify a test so it passes without understanding why it failed.

## Language boundary (strict)

- **English** for everything in code: names, files, tables, columns, endpoints, comments, docstrings, logs, tests, commits, internal docs, this file.
- **Spanish** for everything the end user sees: UI text, validation messages, client emails, demo data, prompt bodies sent to the LLM (we want Spanish answers). Prompt frontmatter and comments stay English.

Details and examples: `docs/conventions.md`.

## Key commands

```bash
docker compose -f infra/docker-compose.yml up -d   # postgres (5433) + redis (6379)
cd apps/api && uv sync                              # install deps
uv run alembic upgrade head                         # migrations
uv run uvicorn src.main:app --reload --port 8000    # API  (use 127.0.0.1, not localhost)
uv run celery -A src.workers.celery_app worker --loglevel=info   # worker (separate terminal)
uv run pytest                                        # tests  (-k tenant / --cov=src)
```

## When to stop and ask before acting

Propose a plan instead of executing if the task: changes an adapter used by all clients; modifies a prompt already in production; touches tenant logic; adds a new third-party SDK; violates an anti-pattern above; or spans more than three files across different modules.

## Handoff on commit

When you make a commit, **update `HANDOFF.md`** in the same commit: current state, next steps, and any gotchas. It is the team's "where things stand" file for whoever continues the work — keep it short and current, overwrite stale entries (git keeps the history).

Where things live: **`HANDOFF.md`** = team-facing, where the work stands now (committed). **`docs/` + this file** = durable architecture and conventions (committed). Personal, machine-local session continuity is a separate concern and never goes in the repo.

## Documentation map (read on demand)

| Read this | Before you… |
|---|---|
| `docs/architecture.md` | …need the folder layout, stack/versions, or the rationale (why pipeline, why JSONB, why Celery, why the tenant header) |
| `docs/pipeline.md` | …touch or add a pipeline step (the 11 real steps, the step/orchestrator pattern) |
| `docs/adapters.md` | …touch or add an adapter (interfaces, current providers, how to add one) |
| `docs/multitenancy.md` | …touch tenant isolation (mixin, session, the isolation test rule) |
| `docs/conventions.md` | …write code: language rules, Python style, naming, errors, migrations, testing, prompts, traceability, glossary |
| `docs/DECISIONS/` | …understand or record an architecture decision (ADRs) |

## How to work with me

Give one bounded task per session, name the specific file, say whether tests are needed and whether the task is exploratory or executive. I will read the relevant `docs/` file first, follow existing patterns before inventing new ones, warn you if a task violates a principle, write tests for what I add unless told otherwise, and keep the docs in sync when architecture changes.

## Current state

The codebase is well past the original scaffold. Working: the 11-step pipeline end to end, all six adapter families (LLM default `groq`, plus `claude`/`openai`/`gemini`; STT `groq_whisper`; TTS `elevenlabs`; MAM/storage/vector with `mock` defaults), the versioned `PromptStore`, multi-tenant models and migrations through `0007` + the `tv_pieces` column, and routes beyond `projects` (assignments, kpis, cesta, cola, grafismo, montaje, audio_mix, archivo, highlights, generar_pieza). Recent direction is vision-first segment selection (Gemini scores frames, code picks cuts). Frontend is a separate repo.
