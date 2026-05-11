import asyncio
import logging
from uuid import UUID

from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="pipeline.process_project")
def process_project(self, project_id: str, tenant_id: str) -> dict:
    """Run the full processing pipeline for a project.

    Called by the HTTP endpoint after a video is registered.
    Runs asynchronously inside a synchronous Celery worker via asyncio.run().
    """
    logger.info("Starting pipeline for project %s (tenant %s)", project_id, tenant_id)
    try:
        result = asyncio.run(_run(UUID(project_id), tenant_id))
        logger.info("Pipeline completed for project %s", project_id)
        return result
    except Exception as exc:
        logger.exception("Pipeline failed for project %s: %s", project_id, exc)
        raise self.retry(exc=exc)


async def _run(project_id: UUID, tenant_id: str) -> dict:
    # Dispose the module-level connection pool so asyncpg creates fresh
    # connections in this event loop. Required because Celery workers run
    # asyncio.run() in a new loop while the engine was created in a different one.
    from src.db.session import engine
    await engine.dispose()

    from src.orchestrator.pipeline import build_pipeline

    pipeline = build_pipeline()
    state = await pipeline.run(project_id, tenant_id)
    return {
        "project_id": str(project_id),
        "status": "completed",
        "composed_video_key": state.composed_video_key,
        "voiceover_audio_key": state.voiceover_audio_key,
    }
