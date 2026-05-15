"""add content_embeddings table for semantic vector search

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "content_embeddings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        # 1024-dim float4 vector — matches voyage-3 output
        sa.Column("embedding", sa.Text(), nullable=False),  # stored as vector via raw SQL below
        sa.Column("metadata", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Alter to proper vector type after table creation (pgvector must exist first)
    op.execute("ALTER TABLE content_embeddings ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector(1024)")

    op.create_index("ix_content_embeddings_tenant_id", "content_embeddings", ["tenant_id"])

    # HNSW index for fast approximate nearest-neighbour with cosine distance
    op.execute(
        "CREATE INDEX ix_content_embeddings_hnsw "
        "ON content_embeddings USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("content_embeddings")
