from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_tenant_id
from src.db.session import tenant_session
from src.models.editorial_package import EditorialPackage
from src.models.pipeline_run import PipelineRun, PipelineStepRun
from src.models.project import Project, ProjectStatus
from src.models.video import Video

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    title: str


class ProjectOut(BaseModel):
    id: UUID
    title: str
    status: ProjectStatus
    tenant_id: str
    video_key: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VideoRegister(BaseModel):
    original_filename: str


class VideoOut(BaseModel):
    id: UUID
    project_id: UUID
    original_filename: str
    storage_key: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StepOut(BaseModel):
    step_name: str
    position: int
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    result: dict | None

    model_config = {"from_attributes": True}


class PipelineStatusOut(BaseModel):
    run_id: UUID
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    steps: list[StepOut]


class EditorialPackageOut(BaseModel):
    id: UUID
    project_id: UUID
    web_article: str | None
    tweet: str | None
    executive_summary: str | None
    angle_proposals: list | None
    voiceover_script: str | None
    voiceover_audio_key: str | None
    composed_video_key: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Session dependency — reuses tenant_session with the header tenant_id
# ---------------------------------------------------------------------------

async def _get_session(tenant_id: str = Depends(get_tenant_id)):
    async with tenant_session(tenant_id) as session:
        yield session


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Project:
    project = Project(
        id=uuid4(),
        tenant_id=tenant_id,
        title=body.title,
        status=ProjectStatus.pending,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> list[Project]:
    rows = await session.scalars(Project.query_for_tenant(tenant_id))
    return list(rows)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Project:
    project = await _get_or_404(session, project_id, tenant_id)
    return project


@router.post("/{project_id}/videos", response_model=VideoOut, status_code=201)
async def register_video(
    project_id: UUID,
    body: VideoRegister,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Video:
    project = await _get_or_404(session, project_id, tenant_id)

    storage_key = f"videos/{project_id}/{body.original_filename}"
    video = Video(
        id=uuid4(),
        project_id=project_id,
        tenant_id=tenant_id,
        original_filename=body.original_filename,
        storage_key=storage_key,
    )
    session.add(video)

    # Keep project.video_key in sync for quick access
    project.video_key = storage_key
    project.status = ProjectStatus.processing

    await session.commit()
    await session.refresh(video)
    return video


@router.post("/{project_id}/pipeline/run", status_code=202)
async def trigger_pipeline(
    project_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> dict:
    project = await _get_or_404(session, project_id, tenant_id)
    if not project.video_key:
        raise HTTPException(status_code=422, detail="Register a video before running the pipeline")

    from src.workers.pipeline_tasks import process_project
    task = process_project.delay(str(project_id), tenant_id)

    return {"task_id": task.id, "project_id": str(project_id), "status": "queued"}


@router.get("/{project_id}/pipeline/status", response_model=PipelineStatusOut)
async def get_pipeline_status(
    project_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> PipelineStatusOut:
    await _get_or_404(session, project_id, tenant_id)

    run = await session.scalar(
        select(PipelineRun)
        .where(PipelineRun.project_id == project_id, PipelineRun.tenant_id == tenant_id)
        .order_by(PipelineRun.created_at.desc())
    )
    if run is None:
        raise HTTPException(status_code=404, detail="No pipeline run found for this project")

    steps = list(await session.scalars(
        select(PipelineStepRun)
        .where(PipelineStepRun.pipeline_run_id == run.id)
        .order_by(PipelineStepRun.position)
    ))

    return PipelineStatusOut(
        run_id=run.id,
        status=run.status.value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        steps=[StepOut.model_validate(s) for s in steps],
    )


@router.get("/{project_id}/results", response_model=EditorialPackageOut)
async def get_results(
    project_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> EditorialPackage:
    await _get_or_404(session, project_id, tenant_id)

    pkg = await session.scalar(
        select(EditorialPackage).where(
            EditorialPackage.project_id == project_id,
            EditorialPackage.tenant_id == tenant_id,
        )
    )
    if pkg is None:
        raise HTTPException(status_code=404, detail="Results not ready yet")
    return pkg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(session: AsyncSession, project_id: UUID, tenant_id: str) -> Project:
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
