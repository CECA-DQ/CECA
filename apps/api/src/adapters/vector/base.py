from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """A single result from a semantic search query."""
    id: str
    score: float        # cosine similarity — 1.0 is identical, 0.0 is unrelated
    metadata: dict = field(default_factory=dict)


class VectorAdapter(ABC):
    """Abstract interface for vector/semantic search backends.

    Implementations: mock.py (tests), pgvector.py (production).
    Resolve with get_vector_adapter() from factory.py.

    The adapter is responsible for generating embeddings internally.
    Callers only deal with plain text — not with vectors.
    """

    @abstractmethod
    async def index(
        self,
        id: str,
        text: str,
        tenant_id: str,
        metadata: dict | None = None,
    ) -> None:
        """Embed the text and store it under the given id.

        If the id already exists, the entry is overwritten.
        """
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        tenant_id: str,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Return the most semantically similar entries for this tenant."""
        ...

    @abstractmethod
    async def delete(self, id: str, tenant_id: str) -> None:
        """Remove an entry from the index. Silent if it does not exist."""
        ...
