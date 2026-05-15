"""add fts_index table for PostgreSQL full-text search (no ML dependencies)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fts_index",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("metadata", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "tsv",
            sa.Text(),  # actually tsvector, set via raw SQL below
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Convert tsv column to proper tsvector type and populate from body
    op.execute("ALTER TABLE fts_index ALTER COLUMN tsv TYPE tsvector USING to_tsvector('spanish', body)")

    # Trigger to keep tsv in sync with body on insert/update
    op.execute("""
        CREATE FUNCTION fts_index_tsv_trigger() RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('spanish', NEW.body);
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER fts_index_tsv_update
        BEFORE INSERT OR UPDATE ON fts_index
        FOR EACH ROW EXECUTE FUNCTION fts_index_tsv_trigger()
    """)

    op.create_index("ix_fts_index_tenant_id", "fts_index", ["tenant_id"])
    op.execute("CREATE INDEX ix_fts_index_tsv ON fts_index USING gin(tsv)")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS fts_index_tsv_update ON fts_index")
    op.execute("DROP FUNCTION IF EXISTS fts_index_tsv_trigger")
    op.drop_table("fts_index")
