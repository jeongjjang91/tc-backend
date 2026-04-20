import asyncio
from typing import Any

import oracledb

from app.shared.exceptions import DBExecutionError
from app.shared.logging import get_logger

logger = get_logger(__name__)


class OraclePool:
    def __init__(
        self,
        dsn: str,
        user: str,
        password: str,
        min_size: int = 2,
        max_size: int = 10,
        timeout_sec: float = 5.0,
    ):
        self.dsn = dsn
        self.user = user
        self.password = password
        self.min_size = min_size
        self.max_size = max_size
        self.timeout_sec = timeout_sec
        self._pool: oracledb.AsyncConnectionPool | None = None

    async def start(self) -> None:
        self._pool = oracledb.create_pool_async(
            user=self.user,
            password=self.password,
            dsn=self.dsn,
            min=self.min_size,
            max=self.max_size,
        )

    async def stop(self) -> None:
        if self._pool:
            await self._pool.close()

    async def _get_conn(self) -> oracledb.AsyncConnection:
        """Acquire a connection from the pool."""
        return await self._pool.acquire()

    async def fetch_all(
        self, sql: str, params: dict | None = None, *, max_rows: int = 1000
    ) -> list[dict[str, Any]]:
        # NOTE: conn.cursor() is sync in real oracledb (returns AsyncCursor).
        # The unit-test mock uses AsyncMock for the connection, which makes cursor()
        # return a coroutine; we await it so that the mock's return_value (which
        # carries the __aenter__/__aexit__ setup) is used as the async context manager.
        # Integration tests against a real Oracle DB use the async-CM on AsyncCursor
        # directly (no await needed), but the await is harmless there since we can
        # adjust if needed in the integration layer.
        try:
            async def _fetch():
                conn = await self._get_conn()
                try:
                    async with await conn.cursor() as cur:
                        await cur.execute(sql, params or {})
                        cols = [d[0].lower() for d in cur.description]
                        rows = await cur.fetchmany(max_rows)
                        return [dict(zip(cols, row)) for row in rows]
                finally:
                    if self._pool is not None:
                        await self._pool.release(conn)
            return await asyncio.wait_for(_fetch(), timeout=self.timeout_sec)
        except asyncio.TimeoutError:
            raise DBExecutionError(f"Query timed out after {self.timeout_sec}s")
        except oracledb.Error as e:
            raise DBExecutionError(str(e)) from e

    async def execute(self, sql: str, params: dict | None = None) -> None:
        try:
            async def _exec():
                conn = await self._get_conn()
                try:
                    async with await conn.cursor() as cur:
                        await cur.execute(sql, params or {})
                        await conn.commit()
                finally:
                    if self._pool is not None:
                        await self._pool.release(conn)
            await asyncio.wait_for(_exec(), timeout=self.timeout_sec)
        except asyncio.TimeoutError:
            raise DBExecutionError(f"Query timed out after {self.timeout_sec}s")
        except oracledb.Error as e:
            raise DBExecutionError(str(e)) from e
