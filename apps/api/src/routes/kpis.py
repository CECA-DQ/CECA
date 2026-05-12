from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_tenant_id
from src.db.session import tenant_session
from src.models.assignment import Assignment, AssignmentSection, AssignmentStatus

router = APIRouter(prefix="/kpis", tags=["kpis"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class KPIsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_reporter: dict[str, int]
    by_section: dict[str, int]
    approval_rate: float  # percentage of aprobada + publicada over total


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------

async def _get_session(tenant_id: str = Depends(get_tenant_id)):
    async with tenant_session(tenant_id) as session:
        yield session


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("", response_model=KPIsOut)
async def get_kpis(
    reference_date: date | None = Query(default=None, description="Filter by day (YYYY-MM-DD). Defaults to today."),
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> KPIsOut:
    target = reference_date or date.today()
    day_start = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    day_end = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=timezone.utc)

    base_q = select(Assignment).where(
        Assignment.tenant_id == tenant_id,
        Assignment.created_at >= day_start,
        Assignment.created_at <= day_end,
    )
    rows = list(await session.scalars(base_q))

    total = len(rows)

    by_status: dict[str, int] = {s.value: 0 for s in AssignmentStatus}
    by_reporter: dict[str, int] = {"redactor_1": 0, "redactor_2": 0, "redactor_3": 0}
    by_section: dict[str, int] = {s.value: 0 for s in AssignmentSection}

    for row in rows:
        by_status[row.status.value] = by_status.get(row.status.value, 0) + 1
        by_reporter[row.reporter_id] = by_reporter.get(row.reporter_id, 0) + 1
        by_section[row.section.value] = by_section.get(row.section.value, 0) + 1

    approved = by_status.get("aprobada", 0) + by_status.get("publicada", 0)
    approval_rate = round((approved / total * 100), 1) if total > 0 else 0.0

    return KPIsOut(
        total=total,
        by_status=by_status,
        by_reporter=by_reporter,
        by_section=by_section,
        approval_rate=approval_rate,
    )
