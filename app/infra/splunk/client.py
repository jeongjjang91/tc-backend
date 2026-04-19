from __future__ import annotations
import asyncio
import httpx
from app.shared.logging import get_logger

logger = get_logger(__name__)

_POLL_INTERVAL = 1.0
_MAX_POLLS = 30


class SplunkClient:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        index: str,
        timeout_sec: float = 30.0,
    ) -> None:
        self.base_url = f"https://{host}:{port}"
        self.index = index
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout_sec

    async def search(
        self,
        query: str,
        earliest: str = "-24h",
        latest: str = "now",
    ) -> list[dict]:
        try:
            async with httpx.AsyncClient(
                headers=self._headers,
                verify=False,
                timeout=self._timeout,
            ) as http:
                sid = await self._create_job(http, query, earliest, latest)
                return await self._poll_results(http, sid)
        except httpx.HTTPError as exc:
            logger.warning("splunk_search_error", error=str(exc))
            return []

    async def _create_job(
        self, http: httpx.AsyncClient, query: str, earliest: str, latest: str
    ) -> str:
        resp = http.build_request(
            "POST",
            f"{self.base_url}/services/search/jobs",
            data={"search": query, "earliest_time": earliest, "latest_time": latest, "output_mode": "json"},
        )
        response = await http.send(resp)
        response.raise_for_status()
        return response.json()["sid"]

    async def _poll_results(self, http: httpx.AsyncClient, sid: str) -> list[dict]:
        url = f"{self.base_url}/services/search/jobs/{sid}/results"
        for _ in range(_MAX_POLLS):
            resp = await http.get(url, params={"output_mode": "json"})
            if resp.status_code == 204:
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            resp.raise_for_status()
            return resp.json().get("results", [])
        logger.warning("splunk_poll_timeout", sid=sid)
        return []
