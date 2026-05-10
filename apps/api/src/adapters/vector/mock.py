from .base import VectorAdapter, SearchResult


class MockVectorAdapter(VectorAdapter):
    """In-memory vector adapter for tests and demos.

    Does not generate real embeddings — uses substring matching as a proxy.
    Scores are synthetic (1.0 for exact match, 0.5 for partial, 0.0 for none).
    """

    def __init__(self) -> None:
        # {tenant_id: {id: {"text": str, "metadata": dict}}}
        self._store: dict[str, dict[str, dict]] = {}

    async def index(self, id: str, text: str, tenant_id: str, metadata: dict | None = None) -> None:
        self._store.setdefault(tenant_id, {})[id] = {
            "text": text,
            "metadata": metadata or {},
        }

    async def search(self, query: str, tenant_id: str, limit: int = 10) -> list[SearchResult]:
        entries = self._store.get(tenant_id, {})
        q = query.lower()
        results: list[SearchResult] = []

        for id, entry in entries.items():
            text = entry["text"].lower()
            if q == text:
                score = 1.0
            elif q in text:
                score = 0.5
            else:
                continue
            results.append(SearchResult(id=id, score=score, metadata=entry["metadata"]))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    async def delete(self, id: str, tenant_id: str) -> None:
        self._store.get(tenant_id, {}).pop(id, None)
