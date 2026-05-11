from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
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
    original_filename: str | None = None
    source_url: str | None = None

    @property
    def is_url(self) -> bool:
        return bool(self.source_url and self.source_url.startswith(("http://", "https://")))

    def resolved_filename(self) -> str:
        if self.is_url:
            return self.source_url.split("?")[0].rstrip("/").split("/")[-1] or "video"
        return self.original_filename or "video"

    def resolved_storage_key(self, project_id) -> str:
        if self.is_url:
            return self.source_url
        return f"videos/{project_id}/{self.original_filename}"


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
    # Computed fields — not stored in DB, added by the route handler
    article: str | None = None
    angles: list | None = None
    video_url: str | None = None

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
    if not body.original_filename and not body.source_url:
        raise HTTPException(status_code=422, detail="Provide either original_filename or source_url")

    project = await _get_or_404(session, project_id, tenant_id)

    storage_key = body.resolved_storage_key(project_id)
    video = Video(
        id=uuid4(),
        project_id=project_id,
        tenant_id=tenant_id,
        original_filename=body.resolved_filename(),
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
    from uuid import uuid4 as _uuid4
    from src.models.pipeline_run import PipelineRun, PipelineStepRun, RunStatus
    from src.orchestrator.pipeline import PIPELINE_STEP_NAMES

    project = await _get_or_404(session, project_id, tenant_id)
    if not project.video_key:
        raise HTTPException(status_code=422, detail="Register a video before running the pipeline")

    # Create PipelineRun and all step records synchronously so the status
    # endpoint returns immediately — before the Celery worker even starts.
    pipeline_run_id = _uuid4()
    session.add(PipelineRun(
        id=pipeline_run_id,
        project_id=project_id,
        tenant_id=tenant_id,
        status=RunStatus.pending,
    ))
    for position, step_name in enumerate(PIPELINE_STEP_NAMES):
        session.add(PipelineStepRun(
            id=_uuid4(),
            pipeline_run_id=pipeline_run_id,
            tenant_id=tenant_id,
            step_name=step_name,
            position=position,
            status=RunStatus.pending,
        ))
    await session.commit()

    from src.workers.pipeline_tasks import process_project
    task = process_project.delay(str(project_id), tenant_id, str(pipeline_run_id))

    return {"task_id": task.id, "project_id": str(project_id), "pipeline_run_id": str(pipeline_run_id), "status": "queued"}


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
) -> EditorialPackageOut:
    await _get_or_404(session, project_id, tenant_id)

    pkg = await session.scalar(
        select(EditorialPackage).where(
            EditorialPackage.project_id == project_id,
            EditorialPackage.tenant_id == tenant_id,
        )
    )
    if pkg is None:
        raise HTTPException(status_code=404, detail="Results not ready yet")

    out = EditorialPackageOut.model_validate(pkg)
    # Frontend expects "article" and "angles" — map from DB column names
    out.article = pkg.web_article
    out.angles = pkg.angle_proposals

    # Only set video_url if the file actually exists in storage
    if pkg.composed_video_key:
        from src.adapters.storage.factory import get_storage_adapter
        if await get_storage_adapter().exists(pkg.composed_video_key):
            out.video_url = f"/projects/{project_id}/video?tenant={tenant_id}"  # relative — frontend prepends API base URL

    return out


@router.get("/{project_id}/video")
async def get_video(
    project_id: UUID,
    x_tenant_id: str = Header(default=""),
    tenant: str = Query(default=""),
) -> StreamingResponse:
    """Stream the composed MP4 for a project.

    Accepts tenant identity from the X-Tenant-ID header OR the ?tenant= query
    param so the browser <video> element (which cannot set headers) can use it.
    """
    effective_tenant = (x_tenant_id or tenant).strip()
    if not effective_tenant:
        raise HTTPException(status_code=401, detail="Tenant identity required")

    async with tenant_session(effective_tenant) as session:
        project = await session.scalar(
            select(Project).where(
                Project.id == project_id,
                Project.tenant_id == effective_tenant,
            )
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        pkg = await session.scalar(
            select(EditorialPackage).where(
                EditorialPackage.project_id == project_id,
                EditorialPackage.tenant_id == effective_tenant,
            )
        )
        if pkg is None or not pkg.composed_video_key:
            raise HTTPException(status_code=404, detail="Video not ready yet")

    from src.adapters.storage.factory import get_storage_adapter
    storage = get_storage_adapter()

    if not await storage.exists(pkg.composed_video_key):
        raise HTTPException(status_code=404, detail="Video file not found in storage")

    video_bytes = await storage.download(pkg.composed_video_key)

    return StreamingResponse(
        iter([video_bytes]),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f"inline; filename=\"project_{project_id}.mp4\"",
            "Content-Length": str(len(video_bytes)),
            "Accept-Ranges": "bytes",
        },
    )


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
