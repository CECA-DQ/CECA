import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum as SAEnum, Float, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TenantOwnedMixin


class TipoSegmento(str, enum.Enum):
    clip = "clip"
    segmento_bruto = "segmento_bruto"
    imagen = "imagen"


class SegmentoArchivo(Base, TenantOwnedMixin):
    """A single indexed unit from the media archive: clip, bruto segment, or image."""

    __tablename__ = "segmentos_archivo"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tipo: Mapped[TipoSegmento] = mapped_column(
        SAEnum(TipoSegmento, name="tipo_segmento"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String(500), nullable=False)
    archivo_origen: Mapped[str] = mapped_column(String(1000), nullable=False)
    tiempo_inicio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tiempo_fin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duracion: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    thumbnail_key: Mapped[str | None] = mapped_column(String(1000))
    transcripcion: Mapped[str | None] = mapped_column(Text)
    descripcion_visual: Mapped[str | None] = mapped_column(Text)
    etiquetas: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
