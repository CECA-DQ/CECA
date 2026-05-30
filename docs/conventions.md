# Conventions

How we write code, prompts, migrations, and tests. The hard rules and the language boundary live in the root `CLAUDE.md`; this file is the detail.

## Language: English code, Spanish for the user

The product is sold to Spanish broadcasters, so user-facing text is Spanish. The code is English. The boundary is strict.

- **Always English:** variable/function/class/file/folder names, table/column names, endpoint paths, branch names, commit messages, comments, docstrings, internal logs, test names, migration descriptions, technical error messages, internal docs.
- **Always Spanish:** UI text, validation messages shown to the journalist, client emails, demo data simulating a Spanish newsroom, landing-page copy. Manage user-facing strings via i18n from day one.
- **Prompts:** the body sent to the model is in the language we want back (Spanish). Frontmatter and comments stay English.

```python
class EditorialService:
    """Generates editorial output for a project."""           # English docstring
    async def generate_web_note(self, transcript: str) -> str:
        prompt = self._prompts.render("editorial/web_note", transcript=transcript)  # Spanish body
        response = await self._llm.generate(system=prompt.system, messages=[...])
        return response.text                                   # Spanish, ready for the UI
```

## Python

- Type hints everywhere. Missing type hint = bug.
- Pydantic 2 for input/output validation in every route.
- `async`/`await` for all I/O. Don't mix sync and async without reason.
- No `print` — use the structured logger in `core/logging.py`.
- Inject dependencies through the constructor, not by importing inside functions. Prefer pure functions.
- Relative imports inside a package, absolute imports across packages.
- Functions under 50 lines, files under 300. Past that, refactor.

## Naming

- REST endpoints plural: `/projects`, `/videos`, `/segments`.
- Tables plural snake_case: `projects`, `video_segments`.
- Pydantic/SQLAlchemy models singular PascalCase: `Project`, `VideoSegment`.
- Python files snake_case.

## Errors

- Never catch a bare `Exception` except at the worker's outer boundary. Catch the specific exception you expect.
- Domain errors are defined in `core/errors.py`, each with its code and message.
- In routes, errors translate to `HTTPException` with the correct status code.

## Prompts

Prompts are versioned markdown files under `src/prompts/`, organized by domain, loaded at startup and rendered with Jinja2 (`StrictUndefined` — a missing variable raises immediately). Loaded via the module-level `PromptStore` singleton (`src/services/prompt_store.py`).

File format:

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

Rules: bump `version` on any change; keep the system message stable and the dynamic content in the user message; test with at least three inputs before bumping; explain the change in the commit; log the prompt key and version on every LLM call. Current prompts live in `src/prompts/pipeline/` (`analyze_visual`, `select_segments`, `write_script`, `generate_package`, `generate_tv_pieces`).

## Traceability and cost

Every AI call is recorded for editorial audit, cost optimization, and debugging. Model: `LLMCall` (`src/models/llm_call.py` when present) with provider, model, prompt key/version, token counts, `cost_usd`, `latency_ms`, and truncated request/response payloads — all `TenantOwned`. Services that call the LLM are decorated with `track_llm_call` from `core/observability.py`. An `/admin/costs` dashboard aggregates by day, tenant, step, and prompt; alerts fire above the per-tenant threshold (`cost_alert_threshold_usd` in `config.py`).

## Migrations

SQLAlchemy 2 async + Alembic. Never change the schema without a migration. One migration per logical change — don't bundle.

```bash
cd apps/api
uv run alembic revision --autogenerate -m "add segments duration column"
# review the generated file under src/db/migrations/versions/
uv run alembic upgrade head
```

Rules: reversible whenever possible (irreversible ones carry a comment explaining why); never drop a column without migrating its data first; to add a non-null field to a populated table, do it in three migrations (add nullable → backfill → set not null); index every foreign key, every frequently filtered column, and always `tenant_id`.

## Testing

```
tests/
├── unit/          Pure logic, no I/O — services and steps with adapter stubs
├── integration/   Test DB + adapter stubs
└── e2e/           Full pipeline with small fixtures
```

- Services and pipeline steps are tested with adapter stubs (`tests/fixtures/stubs.py`), never against real APIs.
- E2E runs against a real test DB (testcontainers).
- Coverage target: 80% in services and orchestrator. Concrete adapters need integration coverage, not unit coverage.
- A tenant-isolation test for every `TenantOwned` entity.

```bash
uv run pytest                 # all
uv run pytest tests/unit      # fast
uv run pytest -k "tenant"     # filtered
uv run pytest --cov=src       # coverage
```

## Common recipes

- **New LLM provider:** implement `LLMProvider` in `src/adapters/llm/<name>.py`, add a `case` in `factory.py`, add the value to the `llm_provider` setting, integration test. (Same shape for any adapter — see `adapters.md`.)
- **New pipeline step:** see `pipeline.md`.
- **New prompt:** create `src/prompts/<domain>/<name>.md` with frontmatter, test with three inputs, use via `prompts.render("<domain>/<name>", **vars)`.
- **New domain entity:** SQLAlchemy model (inherit `Base` + `TenantOwnedMixin`) → Pydantic schemas → Alembic migration → repository → service → routes → tests (service unit, routes integration, tenant isolation).
- **Switch the model used in a step:** don't touch step code — change the model in the service config/setting. Per-client differences go in a per-tenant config layer.

## Glossary

Industry terms, kept in Spanish where that is the industry usage.

| Term | Meaning |
|---|---|
| MAM | Media Asset Management — stores and catalogs video + metadata (AVID, Dalet, Cinegy) |
| AVID | Dominant TV production ecosystem (MediaCentral, NEXIS, Interplay, Media Composer, iNews) |
| NEXIS | AVID's shared storage where videos live |
| MediaCentral | Layer that organizes NEXIS and exposes it to external tools via API |
| iNews | AVID's newsroom/rundown system |
| Escaleta (rundown) | The order of pieces in a program |
| Pieza (piece) | A content unit: a news item, report, interview |
| Cola (queue) | An editorially proposed sequence of related pieces |
| Cesta (basket) | The set of resources a journalist gathers to build a piece |
| Brutos (raw footage) | Unedited recorded material |
| Plano (shot) | A continuous camera take |
| Plano de recurso (b-roll) | Footage interleaved with soundbites for visual variety |
| Voz en off (voiceover) | Narration over the images |
| Lower third | On-screen graphic showing the speaker's name |
| Ducking | Lowering the music automatically when there is voice |
| Playout | The system that broadcasts the signal on air |
| Ingesta (ingest) | Bringing new material into the central system |
| Plantilla (template) | The broadcaster's visual identity (typography, colors, intros, lower thirds) |
