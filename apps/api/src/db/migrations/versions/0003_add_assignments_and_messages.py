"""add assignments and assignment_messages tables

Revision ID: 0003
Revises: 015cd699c453
Create Date: 2026-05-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, UUID

revision: str = "0003"
down_revision: Union[str, None] = "015cd699c453"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE assignment_section AS ENUM "
        "('nacional', 'economia', 'deportes', 'internacional', 'sociedad', 'cultura')"
    )
    op.execute(
        "CREATE TYPE schedule_block AS ENUM "
        "('manana', 'magazine', 'mediodia', 'tarde', 'deportes_bloque', 'noche')"
    )
    op.execute(
        "CREATE TYPE assignment_status AS ENUM "
        "('pendiente', 'en_redaccion', 'en_revision', 'aprobada', 'publicada')"
    )

    assignment_section = PgEnum(name="assignment_section", create_type=False)
    schedule_block = PgEnum(name="schedule_block", create_type=False)
    assignment_status = PgEnum(name="assignment_status", create_type=False)

    op.create_table(
        "assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("section", assignment_section, nullable=False),
        sa.Column("topic", sa.String(500), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("reporter_id", sa.String(50), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schedule_block", schedule_block, nullable=False),
        sa.Column("status", assignment_status, nullable=False, server_default="pendiente"),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assignments_tenant_id", "assignments", ["tenant_id"])
    op.create_index("ix_assignments_reporter_id", "assignments", ["reporter_id"])
    op.create_index("ix_assignments_status", "assignments", ["status"])

    op.create_table(
        "assignment_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column(
            "assignment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assignment_messages_tenant_id", "assignment_messages", ["tenant_id"])
    op.create_index("ix_assignment_messages_assignment_id", "assignment_messages", ["assignment_id"])


def downgrade() -> None:
    op.drop_table("assignment_messages")
    op.drop_table("assignments")
    op.execute("DROP TYPE IF EXISTS assignment_status")
    op.execute("DROP TYPE IF EXISTS schedule_block")
    op.execute("DROP TYPE IF EXISTS assignment_section")
