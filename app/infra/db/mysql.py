from __future__ import annotations
import asyncio
from typing import Any

import aiomysql

from app.infra.db.base import DBPool
from app.shared.exceptions import DBExecutionError
from app.shared.logging import get_logger

logger = get_logger(__name__)


class MySQLPool(DBPool):
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        db: str,
        min_size: int = 2,
        max_size: int = 10,
        timeout_sec: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.db = db
        self.min_size = min_size
        self.max_size = max_size
        self.timeout_sec = timeout_sec
        self._pool: aiomysql.Pool | None = None

    async def start(self) -> None:
        self._pool = await aiomysql.create_pool(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            db=self.db,
            minsize=self.min_size,
            maxsize=self.max_size,
            autocommit=False,
            charset="utf8mb4",
        )

    async def stop(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()

    async def fetch_all(
        self, sql: str, params: dict | None = None, *, max_rows: int = 1000
    ) -> list[dict[str, Any]]:
        try:
            async def _fetch():
                async with self._pool.acquire() as conn:
                    async with conn.cursor(aiomysql.DictCursor) as cur:
                        await cur.execute(sql, params or ())
                        return list(await cur.fetchmany(max_rows))
            return await asyncio.wait_for(_fetch(), timeout=self.timeout_sec)
        except asyncio.TimeoutError:
            raise DBExecutionError(f"Query timed out after {self.timeout_sec}s")
        except aiomysql.Error as e:
            raise DBExecutionError(str(e)) from e

    async def execute(self, sql: str, params: dict | None = None) -> None:
        try:
            async def _exec():
                async with self._pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(sql, params or ())
                        await conn.commit()
            await asyncio.wait_for(_exec(), timeout=self.timeout_sec)
        except asyncio.TimeoutError:
            raise DBExecutionError(f"Query timed out after {self.timeout_sec}s")
        except aiomysql.Error as e:
            raise DBExecutionError(str(e)) from e
