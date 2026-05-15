"""add segmentos_archivo table

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, JSONB, UUID

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE tipo_segmento AS ENUM ('clip', 'segmento_bruto', 'imagen')"
    )
    tipo_segmento = PgEnum(name="tipo_segmento", create_type=False)

    op.create_table(
        "segmentos_archivo",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("tipo", tipo_segmento, nullable=False),
        sa.Column("nombre", sa.String(500), nullable=False),
        sa.Column("archivo_origen", sa.String(1000), nullable=False),
        sa.Column("tiempo_inicio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tiempo_fin", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duracion", sa.Float(), nullable=False, server_default="0"),
        sa.Column("thumbnail_key", sa.String(1000), nullable=True),
        sa.Column("transcripcion", sa.Text(), nullable=True),
        sa.Column("descripcion_visual", sa.Text(), nullable=True),
        sa.Column("etiquetas", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_segmentos_archivo_tenant_id", "segmentos_archivo", ["tenant_id"])
    op.create_index("ix_segmentos_archivo_tipo", "segmentos_archivo", ["tipo"])


def downgrade() -> None:
    op.drop_table("segmentos_archivo")
    op.execute("DROP TYPE IF EXISTS tipo_segmento")
