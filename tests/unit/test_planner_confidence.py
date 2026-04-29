import pytest

from app.core.orchestrator.intent_classifier import KeywordIntentClassifier
from app.core.orchestrator.planner import PlannerThresholds, QueryPlanner


def test_classifier_returns_confidence_signal():
    clf = KeywordIntentClassifier()
    prediction = clf.predict("최근 에러 로그 원인 분석해줘")
    assert prediction.label == "log"
    assert prediction.score > 0
    assert prediction.margin >= 0


@pytest.mark.asyncio
async def test_high_confidence_classifier_skips_llm():
    class ExplodingLLM:
        async def complete_json(self, prompt):
            raise AssertionError("LLM should not be called")

    planner = QueryPlanner(
        llm=ExplodingLLM(),
        renderer=object(),
        thresholds=PlannerThresholds(confidence=0.3, margin=0.01, entropy=2.0),
    )
    result = await planner.plan_async("최근 에러 로그 원인 분석해줘", session_id="s1")
    assert result[0].agent == "log"


@pytest.mark.asyncio
async def test_low_confidence_uses_llm_fallback():
    class StubLLM:
        async def complete_json(self, prompt):
            return {"agent": "doc"}

    class StubRenderer:
        def render(self, name, **kwargs):
            return "prompt"

    planner = QueryPlanner(
        llm=StubLLM(),
        renderer=StubRenderer(),
        thresholds=PlannerThresholds(confidence=0.99, margin=0.99, entropy=0.0),
    )
    result = await planner.plan_async("PARAM_X", session_id="s1")
    assert result[0].agent == "doc"
