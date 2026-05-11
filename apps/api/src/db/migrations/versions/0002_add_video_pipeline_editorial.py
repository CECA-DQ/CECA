"""add video, pipeline runs, and editorial package tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("resolution", sa.String(20), nullable=True),
        sa.Column("codec", sa.String(50), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_videos_project_id", "videos", ["project_id"])
    op.create_index("ix_videos_tenant_id", "videos", ["tenant_id"])

    # Create the enum type once explicitly; both tables reference it with create_type=False.
    op.execute("CREATE TYPE run_status AS ENUM ('pending', 'running', 'completed', 'failed')")
    run_status = PgEnum(name="run_status", create_type=False)

    op.create_table(
        "pipeline_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pipeline_runs_project_id", "pipeline_runs", ["project_id"])
    op.create_index("ix_pipeline_runs_tenant_id", "pipeline_runs", ["tenant_id"])

    op.create_table(
        "pipeline_step_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pipeline_run_id", UUID(as_uuid=True), sa.ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("step_name", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_pipeline_step_runs_pipeline_run_id", "pipeline_step_runs", ["pipeline_run_id"])
    op.create_index("ix_pipeline_step_runs_tenant_id", "pipeline_step_runs", ["tenant_id"])

    op.create_table(
        "editorial_packages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("web_article", sa.Text(), nullable=True),
        sa.Column("tweet", sa.Text(), nullable=True),
        sa.Column("executive_summary", sa.Text(), nullable=True),
        sa.Column("angle_proposals", JSONB, nullable=True),
        sa.Column("voiceover_script", sa.Text(), nullable=True),
        sa.Column("voiceover_audio_key", sa.Text(), nullable=True),
        sa.Column("composed_video_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_editorial_packages_project_id", "editorial_packages", ["project_id"], unique=True)
    op.create_index("ix_editorial_packages_tenant_id", "editorial_packages", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("editorial_packages")
    op.drop_table("pipeline_step_runs")
    op.drop_table("pipeline_runs")
    op.execute("DROP TYPE IF EXISTS run_status")
    op.drop_table("videos")
