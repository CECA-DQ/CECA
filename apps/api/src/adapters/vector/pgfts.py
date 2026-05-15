import json
import logging

import asyncpg

from .base import SearchResult, VectorAdapter

logger = logging.getLogger(__name__)


class PgFTSAdapter(VectorAdapter):
    """Full-text search via PostgreSQL tsvector/tsquery.

    Uses the 'spanish' text search configuration so Spanish stems are matched
    correctly. Scores are ts_rank normalised to [0, 1].

    Requires migration 0007 (fts_index table).
    Set VECTOR_PROVIDER=pgfts to activate. No API keys needed.
    """

    def __init__(self, database_url: str) -> None:
        self._dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")

    async def _conn(self) -> asyncpg.Connection:
        return await asyncpg.connect(self._dsn)

    async def index(
        self,
        id: str,
        text: str,
        tenant_id: str,
        metadata: dict | None = None,
    ) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                """
                INSERT INTO fts_index (id, tenant_id, body, metadata)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE SET
                    body      = EXCLUDED.body,
                    metadata  = EXCLUDED.metadata,
                    tenant_id = EXCLUDED.tenant_id,
                    tsv       = to_tsvector('spanish', EXCLUDED.body)
                """,
                id,
                tenant_id,
                text,
                json.dumps(metadata or {}),
            )
        finally:
            await conn.close()

    async def search(
        self,
        query: str,
        tenant_id: str,
        limit: int = 10,
    ) -> list[SearchResult]:
        conn = await self._conn()
        try:
            # plainto_tsquery handles multi-word queries gracefully (AND logic, no syntax errors)
            # Try Spanish first, then English config for bilingual content
            rows = await conn.fetch(
                """
                SELECT id,
                       ts_rank(tsv, plainto_tsquery('spanish', $1))
                       + ts_rank(tsv, plainto_tsquery('english', $1)) AS score,
                       metadata
                FROM   fts_index
                WHERE  tenant_id = $2
                  AND  (tsv @@ plainto_tsquery('spanish', $1)
                        OR tsv @@ plainto_tsquery('english', $1))
                ORDER  BY score DESC
                LIMIT  $3
                """,
                query,
                tenant_id,
                limit,
            )
            if not rows:
                # Fallback: any word from the query via ILIKE (OR logic)
                words = [w.strip() for w in query.split() if len(w.strip()) > 2]
                if words:
                    conditions = " OR ".join(f"body ILIKE '%{w}%'" for w in words)
                    rows = await conn.fetch(
                        f"""
                        SELECT id, 0.1::float AS score, metadata
                        FROM   fts_index
                        WHERE  tenant_id = $1 AND ({conditions})
                        ORDER  BY id
                        LIMIT  $2
                        """,
                        tenant_id,
                        limit,
                    )
        finally:
            await conn.close()

        return [
            SearchResult(
                id=row["id"],
                score=float(row["score"]),
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    async def delete(self, id: str, tenant_id: str) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "DELETE FROM fts_index WHERE id = $1 AND tenant_id = $2",
                id,
                tenant_id,
            )
        finally:
            await conn.close()
