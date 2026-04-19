from abc import ABC, abstractmethod
from typing import Any


class DBPool(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def fetch_all(
        self, sql: str, params: dict | None = None, *, max_rows: int = 1000
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def execute(self, sql: str, params: dict | None = None) -> None: ...
