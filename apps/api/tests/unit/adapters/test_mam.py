"""Unit tests for the MAM adapter.

These tests run against MockMAMAdapter only — no external services needed.
Each test verifies one specific behaviour of the interface.
"""
import pytest

from src.adapters.mam.mock import MockMAMAdapter
from src.core.errors import NotFoundError


@pytest.fixture
def mam() -> MockMAMAdapter:
    return MockMAMAdapter()


async def test_search_returns_matching_assets(mam: MockMAMAdapter) -> None:
    results = await mam.search("rueda", tenant_id="tenant-1")
    assert len(results) == 1
    assert results[0].id == "mock-001"


async def test_search_no_match_returns_empty_list(mam: MockMAMAdapter) -> None:
    results = await mam.search("xxxxxxxxxxx", tenant_id="tenant-1")
    assert results == []


async def test_search_is_case_insensitive(mam: MockMAMAdapter) -> None:
    results = await mam.search("RUEDA", tenant_id="tenant-1")
    assert len(results) == 1


async def test_get_asset_returns_correct_asset(mam: MockMAMAdapter) -> None:
    asset = await mam.get_asset("mock-002", tenant_id="tenant-1")
    assert asset.title == "Acto oficial — inauguración"
    assert asset.duration_seconds == 540.0


async def test_get_asset_unknown_id_raises_not_found(mam: MockMAMAdapter) -> None:
    with pytest.raises(NotFoundError):
        await mam.get_asset("does-not-exist", tenant_id="tenant-1")


async def test_search_limit_is_respected(mam: MockMAMAdapter) -> None:
    # All assets contain the letter 'a' — mock has 3, limit to 2
    results = await mam.search("a", tenant_id="tenant-1", limit=2)
    assert len(results) <= 2
