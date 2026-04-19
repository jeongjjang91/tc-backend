import uuid
import pytest
import respx
import httpx
from app.core.agents.rag.agent import RAGAgent
from app.infra.rag.confluence_client import ConfluenceClient
from app.infra.rag.reranker import TFIDFReranker
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, Context


@pytest.fixture
def llm():
    return InternalLLMProvider(base_url="http://mock-llm", api_key="k", model="test")


@pytest.fixture
def renderer(tmp_path):
    (tmp_path / "rag_query.j2").write_text('q: {{ question }}', encoding="utf-8")
    (tmp_path / "rag_answer.j2").write_text('q: {{ question }}\ndocs: {{ docs }}', encoding="utf-8")
    return PromptRenderer(prompt_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_rag_agent_returns_answer(llm, renderer):
    confluence = ConfluenceClient(base_url="http://mock-conf", token="t", space_key="TC")
    agent = RAGAgent(llm=llm, renderer=renderer, confluence=confluence, reranker=TFIDFReranker())

    with respx.mock:
        respx.get("http://mock-conf/rest/api/content/search").mock(
            return_value=httpx.Response(200, json={"results": [
                {"id": "1", "title": "PARAM_X", "body": {"storage": {"value": "PARAM_X는 온도 파라미터"}}}
            ]})
        )
        respx.post("http://mock-llm/chat/completions").mock(side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"keywords":["PARAM_X"],"query":"PARAM_X 기능"}'}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"PARAM_X는 온도 파라미터입니다[doc_1]","confidence":0.85,"needs_human_review":false}'}}]}),
        ])
        result = await agent.run(
            SubQuery(id="t1", agent="doc", query="PARAM_X 기능이 뭐야?"),
            Context(session_id="s1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert len(result.evidence) == 1
    assert result.confidence == pytest.approx(0.85)
    assert result.evidence[0].source_type == "doc_chunk"


@pytest.mark.asyncio
async def test_rag_agent_no_results_returns_gracefully(llm, renderer):
    confluence = ConfluenceClient(base_url="http://mock-conf", token="t", space_key="TC")
    agent = RAGAgent(llm=llm, renderer=renderer, confluence=confluence, reranker=TFIDFReranker())

    with respx.mock:
        respx.get("http://mock-conf/rest/api/content/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"keywords":[],"query":"없는기능"}'}}]})
        )
        result = await agent.run(
            SubQuery(id="t2", agent="doc", query="없는기능 설명"),
            Context(session_id="s1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert result.confidence == 0.0
    assert result.evidence == []
