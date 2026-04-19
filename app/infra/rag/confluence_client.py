from __future__ import annotations
import re
import httpx
from app.shared.logging import get_logger

logger = get_logger(__name__)


class ConfluenceClient:
    def __init__(self, base_url: str, token: str, space_key: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.space_key = space_key
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
        )

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        try:
            resp = await self._client.get(
                f"{self.base_url}/rest/api/content/search",
                params={
                    "cql": f'space="{self.space_key}" AND text~"{query}"',
                    "limit": limit,
                    "expand": "body.storage",
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "content": self._strip_html(r["body"]["storage"]["value"]),
                    "url": f"{self.base_url}/pages/{r['id']}",
                }
                for r in results
            ]
        except httpx.HTTPError as e:
            logger.error("confluence_search_failed", query=query, error=str(e))
            return []

    @staticmethod
    def _strip_html(html: str) -> str:
        return re.sub(r"<[^>]+>", "", html).strip()
