from unittest.mock import AsyncMock, patch
import pytest
from app.infra.db.oracle import OraclePool


@pytest.mark.asyncio
async def test_fetch_all_returns_list():
    pool = OraclePool(dsn="mock", user="u", password="p")
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchmany.return_value = [("row1",), ("row2",)]
    mock_cursor.description = [("col",)]
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch.object(pool, "_get_conn", return_value=mock_conn):
        rows = await pool.fetch_all("SELECT 1 FROM DUAL")

    assert len(rows) == 2


@pytest.mark.asyncio
async def test_fetch_all_timeout_raises():
    import asyncio
    pool = OraclePool(dsn="mock", user="u", password="p", timeout_sec=0.001)

    async def slow_query(*args, **kwargs):
        await asyncio.sleep(10)

    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.execute = slow_query
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch.object(pool, "_get_conn", return_value=mock_conn):
        with pytest.raises(Exception):
            await pool.fetch_all("SELECT 1 FROM DUAL")
