import logging

from celery import shared_task

from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="pipeline.process_project")
def process_project(self, project_id: str, tenant_id: str) -> dict:
    """Main pipeline task. Receives a project_id and runs all processing steps.

    This task is triggered by the HTTP endpoint after a video is uploaded.
    The pipeline steps will be wired here as they are implemented:

        1. ingest       — download video to storage
        2. transcribe   — audio → text with timestamps (STT)
        3. select       — choose which segments to use (LLM)
        4. write_script — generate voiceover script (LLM)
        5. synthesize   — script → audio (TTS)
        6. compose      — assemble final video (FFmpeg)
        7. package      — generate article, tweet, summary (LLM)
        8. index        — embed content for semantic search (vector)

    Returns a dict with the final project state for the Celery result backend.
    """
    logger.info("Starting pipeline for project %s (tenant %s)", project_id, tenant_id)

    try:
        # Pipeline steps will be called here sequentially.
        # Each step receives and returns the pipeline state.
        # Example (to be implemented):
        #
        # state = PipelineState(project_id=project_id, tenant_id=tenant_id)
        # state = IngestStep(...).execute(state)
        # state = TranscribeStep(...).execute(state)
        # ...

        logger.info("Pipeline completed for project %s", project_id)
        return {"project_id": project_id, "status": "completed"}

    except Exception as exc:
        logger.exception("Pipeline failed for project %s: %s", project_id, exc)
        raise self.retry(exc=exc)
