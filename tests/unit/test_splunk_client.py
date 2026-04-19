import pytest
import respx
import httpx
from app.infra.splunk.client import SplunkClient


@pytest.fixture
def client():
    return SplunkClient(host="splunk.internal", port=8089, token="test-token", index="main")


@respx.mock
@pytest.mark.asyncio
async def test_search_returns_events(client):
    # POST create job
    respx.post("https://splunk.internal:8089/services/search/jobs").mock(
        return_value=httpx.Response(201, json={"sid": "abc123"})
    )
    # GET results
    respx.get("https://splunk.internal:8089/services/search/jobs/abc123/results").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"_time": "2024-01-01T00:00:00", "_raw": "ERROR occurred", "host": "h1"},
                ]
            },
        )
    )
    events = await client.search("index=main ERROR")
    assert len(events) == 1
    assert events[0]["host"] == "h1"


@respx.mock
@pytest.mark.asyncio
async def test_search_returns_empty_on_http_error(client):
    respx.post("https://splunk.internal:8089/services/search/jobs").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    events = await client.search("index=main ERROR")
    assert events == []


@respx.mock
@pytest.mark.asyncio
async def test_search_polls_until_done(client):
    respx.post("https://splunk.internal:8089/services/search/jobs").mock(
        return_value=httpx.Response(201, json={"sid": "job1"})
    )
    call_count = 0

    def results_handler(request):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            # First call: job not done yet
            return httpx.Response(204)
        return httpx.Response(200, json={"results": [{"_raw": "done"}]})

    respx.get("https://splunk.internal:8089/services/search/jobs/job1/results").mock(
        side_effect=results_handler
    )
    events = await client.search("index=main")
    assert len(events) == 1
    assert call_count == 2
