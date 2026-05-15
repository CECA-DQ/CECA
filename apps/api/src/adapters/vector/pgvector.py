import json
import logging

import asyncpg
from pgvector.asyncpg import register_vector

from .base import SearchResult, VectorAdapter

logger = logging.getLogger(__name__)


class PgVectorAdapter(VectorAdapter):
    """Semantic search via pgvector.

    Embeddings are generated with Voyage AI (voyage-3, 1024 dims).
    Vectors are stored in the content_embeddings table alongside tenant_id
    and a JSON metadata blob. All filtering beyond tenant isolation is done
    by the caller after getting back IDs and scores.

    Requires migration 0005 (CREATE EXTENSION vector + content_embeddings table).
    Set VECTOR_PROVIDER=pgvector and VOYAGE_API_KEY in .env to activate.
    """

    def __init__(
        self,
        database_url: str,
        voyage_api_key: str,
        model: str = "voyage-3",
        dims: int = 1024,
    ) -> None:
        # asyncpg expects postgresql:// not postgresql+asyncpg://
        self._dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._voyage_api_key = voyage_api_key
        self._model = model
        self._dims = dims

    async def _conn(self) -> asyncpg.Connection:
        conn = await asyncpg.connect(self._dsn)
        await register_vector(conn)
        return conn

    async def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        if self._model.startswith("local/"):
            import asyncio
            from fastembed import TextEmbedding
            model_name = self._model.removeprefix("local/")
            loop = asyncio.get_event_loop()

            def _run() -> list[list[float]]:
                model = TextEmbedding(model_name=model_name)
                return [e.tolist() for e in model.embed(texts)]

            return await loop.run_in_executor(None, _run)
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self._voyage_api_key)
        response = await client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self._dims,
        )
        return [item.embedding for item in response.data]

    async def index(
        self,
        id: str,
        text: str,
        tenant_id: str,
        metadata: dict | None = None,
    ) -> None:
        embeddings = await self._embed([text], input_type="document")
        conn = await self._conn()
        try:
            await conn.execute(
                """
                INSERT INTO content_embeddings (id, tenant_id, embedding, metadata)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    metadata  = EXCLUDED.metadata,
                    tenant_id = EXCLUDED.tenant_id
                """,
                id,
                tenant_id,
                embeddings[0],
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
        embeddings = await self._embed([query], input_type="query")
        conn = await self._conn()
        try:
            rows = await conn.fetch(
                """
                SELECT id, 1 - (embedding <=> $1) AS score, metadata
                FROM content_embeddings
                WHERE tenant_id = $2
                ORDER BY embedding <=> $1
                LIMIT $3
                """,
                embeddings[0],
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
                "DELETE FROM content_embeddings WHERE id = $1 AND tenant_id = $2",
                id,
                tenant_id,
            )
        finally:
            await conn.close()
