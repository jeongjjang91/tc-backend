import uuid
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock
from app.core.agents.log.agent import SplunkAgent
from app.infra.splunk.client import SplunkClient
from app.infra.db.review_repo import ReviewRepository
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, Context


@pytest.fixture
def llm():
    return InternalLLMProvider(base_url="http://mock-llm", api_key="k", model="test")


@pytest.fixture
def renderer(tmp_path):
    (tmp_path / "splunk_query.j2").write_text(
        "question: {{ question }} index: {{ index }}", encoding="utf-8"
    )
    (tmp_path / "log_analysis.j2").write_text(
        "q: {{ question }} events: {{ events }}", encoding="utf-8"
    )
    return PromptRenderer(prompt_dir=str(tmp_path))


@pytest.fixture
def mock_review_repo():
    repo = MagicMock(spec=ReviewRepository)
    repo.create_pending = AsyncMock(return_value=1)
    return repo


@pytest.mark.asyncio
async def test_splunk_agent_returns_analysis(llm, renderer, mock_review_repo):
    splunk = SplunkClient(host="splunk.internal", port=8089, token="t", index="tc_logs")
    agent = SplunkAgent(
        llm=llm,
        renderer=renderer,
        splunk=splunk,
        review_repo=mock_review_repo,
        review_threshold=0.6,
    )

    with respx.mock:
        respx.post("https://splunk.internal:8089/services/search/jobs").mock(
            return_value=httpx.Response(201, json={"sid": "job1"})
        )
        respx.get("https://splunk.internal:8089/services/search/jobs/job1/results").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"_raw": "ERROR CODE=E001 temperature fault", "_time": "2024-01-01T00:00:00", "host": "tc01"},
                        {"_raw": "ERROR CODE=E001 temperature fault", "_time": "2024-01-01T00:01:00", "host": "tc01"},
                    ]
                },
            )
        )
        respx.post("http://mock-llm/chat/completions").mock(
            side_effect=[
                httpx.Response(200, json={"choices": [{"message": {"content": '{"query":"index=tc_logs ERROR","earliest":"-24h","latest":"now","rationale":"에러 검색"}'}}]}),
                httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"E001 에러 반복 발생","root_cause":"온도 센서 이상","recommendation":"센서 점검","confidence":0.8,"needs_human_review":false}'}}]}),
            ]
        )
        result = await agent.run(
            SubQuery(id="s1", agent="log", query="tc01 오동작 원인 찾아줘"),
            Context(session_id="sess1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert len(result.evidence) == 2
    assert result.confidence == pytest.approx(0.8)
    assert result.evidence[0].source_type == "log_line"
    mock_review_repo.create_pending.assert_not_called()


@pytest.mark.asyncio
async def test_splunk_agent_low_confidence_creates_review(llm, renderer, mock_review_repo):
    splunk = SplunkClient(host="splunk.internal", port=8089, token="t", index="tc_logs")
    agent = SplunkAgent(
        llm=llm,
        renderer=renderer,
        splunk=splunk,
        review_repo=mock_review_repo,
        review_threshold=0.6,
    )

    with respx.mock:
        respx.post("https://splunk.internal:8089/services/search/jobs").mock(
            return_value=httpx.Response(201, json={"sid": "job2"})
        )
        respx.get("https://splunk.internal:8089/services/search/jobs/job2/results").mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"_raw": "WARN possible issue", "_time": "2024-01-01", "host": "tc02"}]},
            )
        )
        respx.post("http://mock-llm/chat/completions").mock(
            side_effect=[
                httpx.Response(200, json={"choices": [{"message": {"content": '{"query":"index=tc_logs","earliest":"-24h","latest":"now","rationale":"검색"}'}}]}),
                httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"원인 불명확","root_cause":"미상","recommendation":"추가 조사 필요","confidence":0.4,"needs_human_review":true}'}}]}),
            ]
        )
        result = await agent.run(
            SubQuery(id="s2", agent="log", query="이상한 동작 원인"),
            Context(session_id="sess2", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert result.raw_data["needs_human_review"] is True
    mock_review_repo.create_pending.assert_called_once()
