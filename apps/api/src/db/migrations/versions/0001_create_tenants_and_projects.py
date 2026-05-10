"""create tenants and projects tables

Revision ID: 0001
Revises:
Create Date: 2026-05-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "completed", "failed", name="project_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("video_key", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("projects")
    op.execute("DROP TYPE IF EXISTS project_status")
    op.drop_table("tenants")
