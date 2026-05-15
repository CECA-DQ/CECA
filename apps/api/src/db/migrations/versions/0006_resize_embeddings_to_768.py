"""resize content_embeddings vector from 1024 to 384 dims (fastembed multilingual-e5-small)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_content_embeddings_hnsw")
    # Truncate invalid rows from failed indexing attempts before changing column type
    op.execute("TRUNCATE TABLE content_embeddings")
    op.execute(
        "ALTER TABLE content_embeddings "
        "ALTER COLUMN embedding TYPE vector(384) USING embedding::vector(384)"
    )
    op.execute(
        "CREATE INDEX ix_content_embeddings_hnsw "
        "ON content_embeddings USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_content_embeddings_hnsw")
    op.execute(
        "ALTER TABLE content_embeddings "
        "ALTER COLUMN embedding TYPE vector(1024) USING NULL::vector(1024)"
    )
    op.execute(
        "CREATE INDEX ix_content_embeddings_hnsw "
        "ON content_embeddings USING hnsw (embedding vector_cosine_ops)"
    )
