FROM python:3.12-slim

# ffmpeg required for video processing and audio extraction
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install Python dependencies before copying source so this layer is cached
# until pyproject.toml or uv.lock change
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-cache

# Copy application source and Alembic config
COPY apps/api/src ./src
COPY apps/api/alembic.ini ./

ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Default command runs the API.
# The worker service overrides this in its Railway config with:
#   celery -A src.workers.celery_app worker --loglevel=info
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
