from __future__ import annotations
import json
from typing import AsyncIterator
import httpx
from app.infra.llm.base import LLMProvider
from app.shared.exceptions import LLMError
from app.shared.logging import get_logger

logger = get_logger(__name__)


class InternalLLMProvider(LLMProvider):
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def complete(self, prompt: str, **kwargs) -> str:
        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": kwargs.get("temperature", 0.0),
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            raise LLMError(f"LLM API error: {e}") from e

    async def stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "temperature": kwargs.get("temperature", 0.0),
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    data = json.loads(line[6:])
                    delta = data["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta

    async def complete_json(self, prompt: str, schema: dict | None = None, **kwargs) -> dict:
        result = await self.complete(
            prompt + "\n\n반드시 JSON만 출력하세요. 코드블록 없이.",
            temperature=0.0,
            **kwargs,
        )
        try:
            cleaned = result.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM이 유효한 JSON을 반환하지 않음: {e}\n원본: {result}") from e
