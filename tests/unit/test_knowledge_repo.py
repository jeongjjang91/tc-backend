import pytest
from unittest.mock import AsyncMock, MagicMock
from app.infra.db.knowledge_repo import KnowledgeRepository


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetch_all = AsyncMock()
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def repo(mock_pool):
    return KnowledgeRepository(app_pool=mock_pool)


@pytest.mark.asyncio
async def test_search_returns_items(repo, mock_pool):
    mock_pool.fetch_all.return_value = [
        {"item_id": 1, "category": "parameter", "title": "PARAM_TEMP", "content": "온도 파라미터", "keywords": None, "source": None}
    ]
    results = await repo.search("PARAM_TEMP")
    assert len(results) == 1
    assert results[0]["title"] == "PARAM_TEMP"
    mock_pool.fetch_all.assert_called_once()


@pytest.mark.asyncio
async def test_search_with_category(repo, mock_pool):
    mock_pool.fetch_all.return_value = []
    await repo.search("threshold", category="parameter")
    call_args = mock_pool.fetch_all.call_args
    assert "%(category)s" in call_args[0][0]
    assert call_args[0][1]["category"] == "parameter"


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_missing(repo, mock_pool):
    mock_pool.fetch_all.return_value = []
    result = await repo.get_by_id(999)
    assert result is None
