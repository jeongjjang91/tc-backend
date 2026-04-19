import uuid
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock
from app.core.orchestrator.planner import QueryPlanner, _classify_rule
from app.core.orchestrator.executor import QueryExecutor
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, AgentResult, Context


# ── rule-based fallback tests ─────────────────────────────────────────────────

def test_rule_classify_log():
    assert _classify_rule("설비 오동작 원인이 뭐야?") == "log"


def test_rule_classify_doc():
    assert _classify_rule("PARAM_TEMP 기능이 뭐야?") == "doc"


def test_rule_classify_db():
    assert _classify_rule("MODEL_INFO에서 설비 목록 조회해줘") == "db"


# ── LLM-based planner tests ───────────────────────────────────────────────────

@pytest.fixture
def llm():
    return InternalLLMProvider(base_url="http://mock-llm", api_key="k", model="test")


@pytest.fixture
def renderer(tmp_path):
    (tmp_path / "planner.j2").write_text(
        "question: {{ question }}", encoding="utf-8"
    )
    return PromptRenderer(prompt_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_planner_llm_routes_to_doc(llm, renderer):
    planner = QueryPlanner(llm=llm, renderer=renderer)
    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": '{"agent":"doc","rationale":"기능 설명 질문"}'}}]}
            )
        )
        result = await planner.plan_async("PARAM_X 설명해줘", session_id="s1")
    assert len(result) == 1
    assert result[0].agent == "doc"


@pytest.mark.asyncio
async def test_planner_llm_fallback_on_error(llm, renderer):
    planner = QueryPlanner(llm=llm, renderer=renderer)
    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(500, text="error")
        )
        result = await planner.plan_async("설비 오동작 원인", session_id="s1")
    # falls back to rule-based → log
    assert result[0].agent == "log"


# ── executor tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_executor_runs_agent():
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(
        return_value=AgentResult(
            sub_query_id="q1", success=True, evidence=[], raw_data={"answer": "ok"}, confidence=0.9
        )
    )
    executor = QueryExecutor(agent_instances={"db": mock_agent})
    ctx = Context(session_id="s1", trace_id=str(uuid.uuid4()))
    results = await executor.execute([SubQuery(id="q1", agent="db", query="테스트")], ctx)
    assert len(results) == 1
    assert results[0].success


@pytest.mark.asyncio
async def test_executor_returns_error_for_missing_agent():
    executor = QueryExecutor(agent_instances={})
    ctx = Context(session_id="s1", trace_id=str(uuid.uuid4()))
    results = await executor.execute([SubQuery(id="q1", agent="unknown", query="테스트")], ctx)
    assert len(results) == 1
    assert results[0].success is False
