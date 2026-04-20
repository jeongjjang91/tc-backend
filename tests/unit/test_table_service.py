import pytest
from unittest.mock import AsyncMock, MagicMock
from app.infra.db.table_service import TableService, TableViewerError

WHITELIST = {
    "tables": {
        "TC_EQUIPMENT": {
            "columns": ["LINEID", "EQPID", "SERVER_MODEL"],
            "filterable": ["LINEID", "EQPID"],
            "requires_where_clause": False,
        },
        "TC_EQP_PARAM": {
            "columns": ["LINEID", "EQPID", "PARAM_NAME", "PARAM_VALUE"],
            "filterable": ["LINEID", "EQPID", "PARAM_NAME"],
            "requires_where_clause": True,
        },
    }
}


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetch_all = AsyncMock()
    return pool


@pytest.fixture
def svc(mock_pool):
    return TableService(tc_pool=mock_pool, whitelist=WHITELIST)


def test_list_tables(svc):
    tables = svc.list_tables()
    names = [t["name"] for t in tables]
    assert "TC_EQUIPMENT" in names
    assert "TC_EQP_PARAM" in names


@pytest.mark.asyncio
async def test_get_rows_with_cache(svc, mock_pool):
    mock_pool.fetch_all.side_effect = [
        [{"cnt": 3}],
        [{"LINEID": "L01", "EQPID": "E1", "SERVER_MODEL": "M1"},
         {"LINEID": "L01", "EQPID": "E2", "SERVER_MODEL": "M2"},
         {"LINEID": "L01", "EQPID": "E3", "SERVER_MODEL": "M3"}],
    ]
    result = await svc.get_rows("TC_EQUIPMENT", {"LINEID": "L01"}, page=1, page_size=50)
    assert result["total"] == 3
    assert len(result["data"]) == 3
    assert result["from_cache"] is False

    # 두 번째 호출: DB 쿼리 없이 캐시에서
    result2 = await svc.get_rows("TC_EQUIPMENT", {"LINEID": "L01"}, page=1, page_size=50)
    assert result2["from_cache"] is True
    assert mock_pool.fetch_all.call_count == 2  # 첫 번째 호출의 2번만


@pytest.mark.asyncio
async def test_requires_where_raises_without_filter(svc):
    with pytest.raises(TableViewerError, match="필터"):
        await svc.get_rows("TC_EQP_PARAM", {}, page=1, page_size=50)


@pytest.mark.asyncio
async def test_unknown_table_raises(svc):
    with pytest.raises(TableViewerError, match="찾을 수 없습니다"):
        await svc.get_rows("NOT_EXIST", {}, page=1, page_size=50)


def test_unknown_filter_columns_ignored(svc):
    cfg = WHITELIST["tables"]["TC_EQUIPMENT"]
    result = svc._validate_filters(cfg, {"LINEID": "L01", "UNKNOWN_COL": "X"})
    assert "LINEID" in result
    assert "UNKNOWN_COL" not in result


@pytest.mark.asyncio
async def test_pagination_slices_cache(svc, mock_pool):
    rows = [{"LINEID": f"L{i:02d}", "EQPID": f"E{i}", "SERVER_MODEL": "M"} for i in range(10)]
    mock_pool.fetch_all.side_effect = [[{"cnt": 10}], rows]

    p1 = await svc.get_rows("TC_EQUIPMENT", {}, page=1, page_size=3)
    p2 = await svc.get_rows("TC_EQUIPMENT", {}, page=2, page_size=3)

    assert len(p1["data"]) == 3
    assert len(p2["data"]) == 3
    assert p1["data"][0]["LINEID"] != p2["data"][0]["LINEID"]
    assert p1["pages"] == 4
    assert mock_pool.fetch_all.call_count == 2  # 캐시로 p2는 DB 없음
