# Architecture Decision Records

One ADR per significant, hard-to-reverse decision. A principle in the root `CLAUDE.md` may only be violated if the deviation is recorded here first.

Format per file: context, decision, consequences. Name files `NNN-short-title.md`.

## Index

| ADR | Status | Summary |
|---|---|---|
| `001-tech-stack.md` | _to be written_ | Python 3.12 / FastAPI / SQLAlchemy async / Celery / Postgres+pgvector / uv |
| `002-adapter-pattern.md` | _to be written_ | Every external dependency behind an abstract interface in `src/adapters/` |
| `003-multitenant-from-day-one.md` | _to be written_ | `tenant_id` on every client-data table from the first model |

> The rationale for these decisions currently lives in `docs/architecture.md`, `docs/adapters.md`, and `docs/multitenancy.md`. Write the full ADRs when each decision needs its own context/consequences record.
