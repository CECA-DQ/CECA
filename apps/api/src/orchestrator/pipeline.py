import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from src.db.session import tenant_session
from src.models.editorial_package import EditorialPackage
from src.models.pipeline_run import PipelineRun, PipelineStepRun, RunStatus
from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)

# Ordered list of step names that will be registered in the DB before execution.
# Must match the name attribute of each PipelineStep subclass.
PIPELINE_STEP_NAMES = [
    "ingest",
    "transcribe",
    "detect_scenes",
    "analyze_visual",
    "select_segments",
    "write_script",
    "generate_voiceover",
    "compose_video",
    "generate_package",
    "generate_tv_pieces",
    "index_for_search",
]


class Pipeline:
    """Orchestrates step execution and keeps the DB state in sync.

    Responsibilities:
    - Create PipelineRun and PipelineStepRun records before execution.
    - Mark each step running → completed/failed as it executes.
    - Persist step results to PipelineStepRun.result (JSONB).
    - Create EditorialPackage on successful completion.
    - Mark PipelineRun as completed or failed.
    """

    def __init__(self, steps: list[PipelineStep]) -> None:
        self._steps = {step.name: step for step in steps}

    async def run(
        self, project_id: UUID, tenant_id: str, pipeline_run_id: UUID | None = None
    ) -> PipelineState:
        if pipeline_run_id is None:
            pipeline_run_id = await self._create_run(project_id, tenant_id)
        else:
            # Run was pre-created by the HTTP endpoint; just mark it as running.
            await self._finish_run(pipeline_run_id, tenant_id, RunStatus.running)

        video_key = await self._load_video_key(project_id, tenant_id)

        state = PipelineState(
            project_id=project_id,
            tenant_id=tenant_id,
            pipeline_run_id=pipeline_run_id,
            video_key=video_key,
        )

        try:
            for step_name in PIPELINE_STEP_NAMES:
                step = self._steps[step_name]
                await self._mark_step(pipeline_run_id, tenant_id, step_name, RunStatus.running)
                try:
                    state = await step.execute(state)
                    await self._mark_step(
                        pipeline_run_id, tenant_id, step_name, RunStatus.completed,
                        result=state.step_results.get(step_name),
                    )
                    logger.info("Step %s completed for project %s", step_name, project_id)
                except Exception as exc:
                    await self._mark_step(
                        pipeline_run_id, tenant_id, step_name, RunStatus.failed,
                        error=str(exc),
                    )
                    raise

            await self._finish_run(pipeline_run_id, tenant_id, RunStatus.completed)
            await self._save_editorial_package(project_id, tenant_id, state)

        except Exception:
            await self._finish_run(pipeline_run_id, tenant_id, RunStatus.failed)
            raise

        return state

    # ------------------------------------------------------------------
    # Private DB helpers
    # ------------------------------------------------------------------

    async def _create_run(self, project_id: UUID, tenant_id: str) -> UUID:
        run_id = uuid4()
        async with tenant_session(tenant_id) as session:
            run = PipelineRun(
                id=run_id,
                project_id=project_id,
                tenant_id=tenant_id,
                status=RunStatus.running,
                started_at=datetime.now(UTC),
            )
            session.add(run)

            for position, step_name in enumerate(PIPELINE_STEP_NAMES):
                session.add(PipelineStepRun(
                    id=uuid4(),
                    pipeline_run_id=run_id,
                    tenant_id=tenant_id,
                    step_name=step_name,
                    position=position,
                    status=RunStatus.pending,
                ))

            await session.commit()
        return run_id

    async def _mark_step(
        self,
        pipeline_run_id: UUID,
        tenant_id: str,
        step_name: str,
        status: RunStatus,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        async with tenant_session(tenant_id) as session:
            row = await session.scalar(
                select(PipelineStepRun).where(
                    PipelineStepRun.pipeline_run_id == pipeline_run_id,
                    PipelineStepRun.step_name == step_name,
                )
            )
            if row is None:
                return
            row.status = status
            if status == RunStatus.running:
                row.started_at = datetime.now(UTC)
            elif status in (RunStatus.completed, RunStatus.failed):
                row.finished_at = datetime.now(UTC)
            if result is not None:
                row.result = result
            if error is not None:
                row.error_message = error
            await session.commit()

    async def _load_video_key(self, project_id: UUID, tenant_id: str) -> str:
        """Load the video_key registered for this project from the DB."""
        from src.models.project import Project
        async with tenant_session(tenant_id) as session:
            project = await session.scalar(
                select(Project).where(
                    Project.id == project_id,
                    Project.tenant_id == tenant_id,
                )
            )
            return project.video_key or "" if project else ""

    async def _finish_run(
        self, pipeline_run_id: UUID, tenant_id: str, status: RunStatus
    ) -> None:
        async with tenant_session(tenant_id) as session:
            run = await session.get(PipelineRun, pipeline_run_id)
            if run:
                run.status = status
                if status == RunStatus.running:
                    run.started_at = datetime.now(UTC)
                else:
                    run.finished_at = datetime.now(UTC)
                await session.commit()

    async def _save_editorial_package(
        self, project_id: UUID, tenant_id: str, state: PipelineState
    ) -> None:
        pkg = state.editorial_package
        if not pkg:
            return
        async with tenant_session(tenant_id) as session:
            # Upsert: update if a package already exists for this project run.
            existing = await session.scalar(
                select(EditorialPackage).where(EditorialPackage.project_id == project_id)
            )
            if existing:
                target = existing
            else:
                target = EditorialPackage(id=uuid4(), project_id=project_id, tenant_id=tenant_id)
                session.add(target)

            target.web_article = pkg.get("web_article")
            target.tweet = pkg.get("tweet")
            target.executive_summary = pkg.get("executive_summary")
            target.angle_proposals = pkg.get("angle_proposals")
            target.voiceover_script = state.voiceover_script
            target.voiceover_audio_key = state.voiceover_audio_key
            target.composed_video_key = state.composed_video_key
            target.tv_pieces = state.tv_pieces or None
            await session.commit()


def build_pipeline() -> Pipeline:
    """Instantiate the pipeline with all steps in order."""
    from src.orchestrator.steps.analyze_visual import AnalyzeVisualStep
    from src.orchestrator.steps.compose_video import ComposeVideoStep
    from src.orchestrator.steps.detect_scenes import DetectScenesStep
    from src.orchestrator.steps.generate_package import GeneratePackageStep
    from src.orchestrator.steps.generate_tv_pieces import GenerateTVPiecesStep
    from src.orchestrator.steps.generate_voiceover import GenerateVoiceoverStep
    from src.orchestrator.steps.index_for_search import IndexForSearchStep
    from src.orchestrator.steps.ingest import IngestStep
    from src.orchestrator.steps.select_segments import SelectSegmentsStep
    from src.orchestrator.steps.transcribe import TranscribeStep
    from src.orchestrator.steps.write_script import WriteScriptStep

    return Pipeline([
        IngestStep(),
        TranscribeStep(),
        DetectScenesStep(),
        AnalyzeVisualStep(),
        SelectSegmentsStep(),
        WriteScriptStep(),
        GenerateVoiceoverStep(),
        ComposeVideoStep(),
        GeneratePackageStep(),
        GenerateTVPiecesStep(),
        IndexForSearchStep(),
    ])
