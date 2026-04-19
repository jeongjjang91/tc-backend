from abc import ABC, abstractmethod
from typing import AsyncIterator


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, prompt: str, **kwargs) -> str: ...

    @abstractmethod
    async def stream(self, prompt: str, **kwargs) -> AsyncIterator[str]: ...

    @abstractmethod
    async def complete_json(self, prompt: str, schema: dict | None = None, **kwargs) -> dict: ...
