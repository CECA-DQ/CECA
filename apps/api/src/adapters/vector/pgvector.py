from .base import VectorAdapter, SearchResult


class PgVectorAdapter(VectorAdapter):
    """Semantic search via pgvector (PostgreSQL extension).

    Embeddings are generated via the configured embeddings API (Voyage AI or Cohere)
    and stored in a dedicated table alongside tenant_id and metadata.

    To implement:
    1. Create the `content_embeddings` table migration (vector(1024) column).
    2. Inject the embedding client (Voyage AI / Cohere SDK).
    3. Replace the NotImplementedError stubs below with real queries.

    Required settings (add to config.py when implementing):
        VOYAGE_API_KEY or COHERE_API_KEY
        EMBEDDING_MODEL — e.g. 'voyage-large-2' or 'embed-multilingual-v3.0'
        EMBEDDING_DIMENSIONS — must match the vector column size in the migration
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def index(self, id: str, text: str, tenant_id: str, metadata: dict | None = None) -> None:
        raise NotImplementedError("PgVectorAdapter.index not yet implemented")

    async def search(self, query: str, tenant_id: str, limit: int = 10) -> list[SearchResult]:
        raise NotImplementedError("PgVectorAdapter.search not yet implemented")

    async def delete(self, id: str, tenant_id: str) -> None:
        raise NotImplementedError("PgVectorAdapter.delete not yet implemented")
