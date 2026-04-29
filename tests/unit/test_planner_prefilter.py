import pytest

from app.core.agents.smalltalk.agent import SmallTalkAgent
from app.core.orchestrator.planner import QueryPlanner, prefilter
from app.shared.schemas import Context, SubQuery


def test_greeting_routes_to_smalltalk():
    assert prefilter("안녕") == "smalltalk"


def test_empty_routes_to_smalltalk():
    assert prefilter("  ") == "smalltalk"


def test_db_question_passes_prefilter():
    assert prefilter("L01 라인 설비 목록 알려줘") is None


@pytest.mark.asyncio
async def test_planner_prefilter_returns_smalltalk_subquery():
    planner = QueryPlanner()
    result = await planner.plan_async("안녕", session_id="s1")
    assert result[0].agent == "smalltalk"


@pytest.mark.asyncio
async def test_smalltalk_agent_answers_without_evidence():
    agent = SmallTalkAgent()
    result = await agent.run(
        SubQuery(id="q1", agent="smalltalk", query="안녕"),
        Context(session_id="s1", trace_id="t1"),
    )
    assert result.success is True
    assert result.raw_data["answer"]
    assert result.evidence == []
