# CECA

AI assistant for television content production. Backend API — the frontend lives in a separate repository.

See [`CLAUDE.md`](./CLAUDE.md) for full architecture and development guidelines.

## Quick start

```bash
# 1. Infrastructure
docker compose -f infra/docker-compose.yml up -d

# 2. Backend
cd apps/api
uv sync
uv run alembic upgrade head
uv run python scripts/seed_demo_data.py
uv run uvicorn src.main:app --reload --port 8000

# 3. Worker (separate terminal)
cd apps/api
uv run celery -A src.workers.celery_app worker --loglevel=info
```

## Tests

```bash
cd apps/api
uv run pytest              # all
uv run pytest tests/unit   # fast
uv run pytest -k "tenant"  # filtered
uv run pytest --cov=src    # with coverage
```
