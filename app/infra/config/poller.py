import asyncio
from app.infra.db.oracle import OraclePool
from app.shared.logging import get_logger

logger = get_logger(__name__)


class ConfigPoller:
    def __init__(self, app_pool: OraclePool, interval_sec: int = 30):
        self.pool = app_pool
        self.interval = interval_sec
        self._versions: dict[str, int] = {}
        self._callbacks: dict[str, list] = {}

    def on_change(self, scope: str, callback) -> None:
        self._callbacks.setdefault(scope, []).append(callback)

    async def start(self) -> None:
        asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                rows = await self.pool.fetch_all(
                    "SELECT scope, version FROM config_version"
                )
                for row in rows:
                    scope = row["scope"]
                    version = row["version"]
                    if self._versions.get(scope) != version:
                        self._versions[scope] = version
                        for cb in self._callbacks.get(scope, []):
                            try:
                                await cb()
                                logger.info("config_reloaded", scope=scope, version=version)
                            except Exception as e:
                                logger.error("config_reload_failed", scope=scope, error=str(e))
            except Exception as e:
                logger.error("config_poll_failed", error=str(e))
            await asyncio.sleep(self.interval)
