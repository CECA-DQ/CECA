import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TenantOwnedMixin


class AssignmentSection(str, enum.Enum):
    nacional = "nacional"
    economia = "economia"
    deportes = "deportes"
    internacional = "internacional"
    sociedad = "sociedad"
    cultura = "cultura"


class ScheduleBlock(str, enum.Enum):
    manana = "manana"
    magazine = "magazine"
    mediodia = "mediodia"
    tarde = "tarde"
    deportes_bloque = "deportes_bloque"
    noche = "noche"


class AssignmentStatus(str, enum.Enum):
    pendiente = "pendiente"
    en_redaccion = "en_redaccion"
    en_revision = "en_revision"
    aprobada = "aprobada"
    publicada = "publicada"


class Assignment(Base, TenantOwnedMixin):
    """A story assignment created by the Editor Jefe and worked by a Redactor."""

    __tablename__ = "assignments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    section: Mapped[AssignmentSection] = mapped_column(
        SAEnum(AssignmentSection, name="assignment_section"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text)
    reporter_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule_block: Mapped[ScheduleBlock] = mapped_column(
        SAEnum(ScheduleBlock, name="schedule_block"), nullable=False
    )
    status: Mapped[AssignmentStatus] = mapped_column(
        SAEnum(AssignmentStatus, name="assignment_status"),
        default=AssignmentStatus.pendiente,
        nullable=False,
        index=True,
    )
    # Linked pipeline project — set when the redactor starts processing media
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
