import pytest
import respx
import httpx
from app.infra.rag.confluence_client import ConfluenceClient


@pytest.mark.asyncio
async def test_search_returns_chunks():
    client = ConfluenceClient(base_url="http://mock-confluence", token="tok", space_key="TC")
    with respx.mock:
        respx.get("http://mock-confluence/rest/api/content/search").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {
                        "id": "1",
                        "title": "PARAM_X 설명",
                        "body": {"storage": {"value": "<p>PARAM_X는 온도 파라미터입니다.</p>"}},
                    }
                ]
            })
        )
        chunks = await client.search("PARAM_X 기능")
    assert len(chunks) > 0
    assert "PARAM_X" in chunks[0]["content"]
    assert chunks[0]["title"] == "PARAM_X 설명"


@pytest.mark.asyncio
async def test_search_empty_returns_empty():
    client = ConfluenceClient(base_url="http://mock-confluence", token="tok", space_key="TC")
    with respx.mock:
        respx.get("http://mock-confluence/rest/api/content/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        chunks = await client.search("존재하지않는기능")
    assert chunks == []


@pytest.mark.asyncio
async def test_search_http_error_returns_empty():
    client = ConfluenceClient(base_url="http://mock-confluence", token="tok", space_key="TC")
    with respx.mock:
        respx.get("http://mock-confluence/rest/api/content/search").mock(
            return_value=httpx.Response(500)
        )
        chunks = await client.search("query")
    assert chunks == []
