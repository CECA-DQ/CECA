"""Unit tests for the vector adapter.

Run against MockVectorAdapter only — no database or embeddings API needed.
"""
import pytest

from src.adapters.vector.mock import MockVectorAdapter


@pytest.fixture
def vector() -> MockVectorAdapter:
    return MockVectorAdapter()


async def test_index_and_search_roundtrip(vector: MockVectorAdapter) -> None:
    await vector.index("doc-1", "rueda de prensa del ministro", tenant_id="tenant-1")
    results = await vector.search("rueda de prensa del ministro", tenant_id="tenant-1")
    assert len(results) == 1
    assert results[0].id == "doc-1"
    assert results[0].score == 1.0


async def test_search_partial_match_returns_result(vector: MockVectorAdapter) -> None:
    await vector.index("doc-1", "rueda de prensa del ministro", tenant_id="tenant-1")
    results = await vector.search("rueda de prensa", tenant_id="tenant-1")
    assert len(results) == 1
    assert results[0].score == 0.5


async def test_search_no_match_returns_empty(vector: MockVectorAdapter) -> None:
    await vector.index("doc-1", "rueda de prensa", tenant_id="tenant-1")
    results = await vector.search("partido de fútbol", tenant_id="tenant-1")
    assert results == []


async def test_tenant_isolation(vector: MockVectorAdapter) -> None:
    await vector.index("doc-1", "rueda de prensa", tenant_id="tenant-1")
    results = await vector.search("rueda de prensa", tenant_id="tenant-2")
    assert results == []


async def test_delete_removes_entry(vector: MockVectorAdapter) -> None:
    await vector.index("doc-1", "rueda de prensa", tenant_id="tenant-1")
    await vector.delete("doc-1", tenant_id="tenant-1")
    results = await vector.search("rueda de prensa", tenant_id="tenant-1")
    assert results == []


async def test_delete_is_silent_for_missing_entry(vector: MockVectorAdapter) -> None:
    await vector.delete("never-existed", tenant_id="tenant-1")  # must not raise


async def test_overwrite_existing_entry(vector: MockVectorAdapter) -> None:
    await vector.index("doc-1", "texto original", tenant_id="tenant-1")
    await vector.index("doc-1", "texto actualizado", tenant_id="tenant-1")
    results = await vector.search("texto actualizado", tenant_id="tenant-1")
    assert results[0].id == "doc-1"
