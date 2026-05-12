from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_tenant_id
from src.db.session import tenant_session
from src.models.assignment import Assignment, AssignmentSection, AssignmentStatus, ScheduleBlock
from src.models.assignment_message import AssignmentMessage

router = APIRouter(prefix="/assignments", tags=["assignments"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AssignmentCreate(BaseModel):
    section: AssignmentSection
    topic: str = Field(..., max_length=500)
    instructions: str | None = None
    reporter_id: str = Field(..., pattern="^redactor_[123]$")
    priority: int = Field(default=3, ge=1, le=5)
    deadline: datetime | None = None
    schedule_block: ScheduleBlock


class AssignmentOut(BaseModel):
    id: UUID
    tenant_id: str
    section: AssignmentSection
    topic: str
    instructions: str | None
    reporter_id: str
    priority: int
    deadline: datetime | None
    schedule_block: ScheduleBlock
    status: AssignmentStatus
    project_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: UUID
    assignment_id: UUID
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AssignmentDetailOut(AssignmentOut):
    messages: list[MessageOut] = []


class RequestRevisionBody(BaseModel):
    comment: str = Field(..., min_length=1)


class LinkProjectBody(BaseModel):
    project_id: UUID


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------

async def _get_session(tenant_id: str = Depends(get_tenant_id)):
    async with tenant_session(tenant_id) as session:
        yield session


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=AssignmentOut, status_code=201)
async def create_assignment(
    body: AssignmentCreate,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Assignment:
    assignment = Assignment(
        id=uuid4(),
        tenant_id=tenant_id,
        section=body.section,
        topic=body.topic,
        instructions=body.instructions,
        reporter_id=body.reporter_id,
        priority=body.priority,
        deadline=body.deadline,
        schedule_block=body.schedule_block,
        status=AssignmentStatus.pendiente,
    )
    session.add(assignment)
    await session.commit()
    await session.refresh(assignment)
    return assignment


@router.get("", response_model=list[AssignmentOut])
async def list_assignments(
    reporter_id: str | None = Query(default=None),
    section: AssignmentSection | None = Query(default=None),
    status: AssignmentStatus | None = Query(default=None),
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> list[Assignment]:
    q = Assignment.query_for_tenant(tenant_id)
    if reporter_id:
        q = q.where(Assignment.reporter_id == reporter_id)
    if section:
        q = q.where(Assignment.section == section)
    if status:
        q = q.where(Assignment.status == status)
    q = q.order_by(Assignment.priority.asc(), Assignment.created_at.desc())
    rows = await session.scalars(q)
    return list(rows)


@router.get("/{assignment_id}", response_model=AssignmentDetailOut)
async def get_assignment(
    assignment_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> AssignmentDetailOut:
    assignment = await _get_or_404(session, assignment_id, tenant_id)
    messages = list(await session.scalars(
        select(AssignmentMessage)
        .where(
            AssignmentMessage.assignment_id == assignment_id,
            AssignmentMessage.tenant_id == tenant_id,
        )
        .order_by(AssignmentMessage.created_at.asc())
    ))
    out = AssignmentDetailOut.model_validate(assignment)
    out.messages = [MessageOut.model_validate(m) for m in messages]
    return out


@router.post("/{assignment_id}/open", response_model=AssignmentOut)
async def open_assignment(
    assignment_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Assignment:
    """Redactor opens the assignment to start working — transitions to EN_REDACCION."""
    assignment = await _get_or_404(session, assignment_id, tenant_id)
    if assignment.status == AssignmentStatus.pendiente:
        assignment.status = AssignmentStatus.en_redaccion
        await session.commit()
        await session.refresh(assignment)
    return assignment


@router.post("/{assignment_id}/submit", response_model=AssignmentOut)
async def submit_for_review(
    assignment_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Assignment:
    """Redactor sends the piece to the Editor Jefe for review."""
    assignment = await _get_or_404(session, assignment_id, tenant_id)
    if assignment.status != AssignmentStatus.en_redaccion:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot submit from status '{assignment.status.value}'",
        )
    assignment.status = AssignmentStatus.en_revision
    await session.commit()
    await session.refresh(assignment)
    return assignment


@router.post("/{assignment_id}/approve", response_model=AssignmentOut)
async def approve_assignment(
    assignment_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Assignment:
    """Editor Jefe approves the piece."""
    assignment = await _get_or_404(session, assignment_id, tenant_id)
    if assignment.status != AssignmentStatus.en_revision:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot approve from status '{assignment.status.value}'",
        )
    assignment.status = AssignmentStatus.aprobada
    await session.commit()
    await session.refresh(assignment)
    return assignment


@router.post("/{assignment_id}/request-revision", response_model=AssignmentOut)
async def request_revision(
    assignment_id: UUID,
    body: RequestRevisionBody,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Assignment:
    """Editor Jefe sends the piece back with a comment."""
    assignment = await _get_or_404(session, assignment_id, tenant_id)
    if assignment.status != AssignmentStatus.en_revision:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot request revision from status '{assignment.status.value}'",
        )
    assignment.status = AssignmentStatus.en_redaccion
    message = AssignmentMessage(
        id=uuid4(),
        tenant_id=tenant_id,
        assignment_id=assignment_id,
        content=body.comment,
    )
    session.add(message)
    await session.commit()
    await session.refresh(assignment)
    return assignment


@router.patch("/{assignment_id}/link-project", response_model=AssignmentOut)
async def link_project(
    assignment_id: UUID,
    body: LinkProjectBody,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Assignment:
    """Link an existing pipeline project to this assignment."""
    assignment = await _get_or_404(session, assignment_id, tenant_id)
    assignment.project_id = body.project_id
    await session.commit()
    await session.refresh(assignment)
    return assignment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(session: AsyncSession, assignment_id: UUID, tenant_id: str) -> Assignment:
    assignment = await session.scalar(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.tenant_id == tenant_id,
        )
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return assignment
