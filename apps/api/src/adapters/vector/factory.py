from src.config import settings

from .base import VectorAdapter
from .mock import MockVectorAdapter
from .pgfts import PgFTSAdapter
from .pgvector import PgVectorAdapter


def get_vector_adapter() -> VectorAdapter:
    """Return the vector adapter configured in VECTOR_PROVIDER env var."""
    match settings.vector_provider:
        case "mock":
            return MockVectorAdapter()
        case "pgvector":
            return PgVectorAdapter(
                database_url=settings.database_url,
                voyage_api_key=settings.openai_api_key,
                model=settings.embedding_model,
                dims=settings.embedding_dimensions,
            )
        case "pgfts":
            return PgFTSAdapter(database_url=settings.database_url)
        case _:
            raise ValueError(f"Unknown vector provider: {settings.vector_provider!r}")
