from app.shared.schemas import SubQuery, AgentResult, Evidence

def test_sub_query_defaults():
    q = SubQuery(id="t1", agent="db", query="A 설비 PARAM_X?")
    assert q.depends_on == []

def test_agent_result_requires_evidence_list():
    e = Evidence(
        id="ev1",
        source_type="db_row",
        content="PARAM_X=Y",
        metadata={"table": "PARAMETER"}
    )
    r = AgentResult(
        sub_query_id="t1",
        success=True,
        evidence=[e],
        raw_data=None,
        confidence=0.9,
        error=None,
    )
    assert r.confidence == 0.9
    assert len(r.evidence) == 1

def test_confidence_range_validated():
    import pytest
    with pytest.raises(Exception):
        AgentResult(
            sub_query_id="t1", success=True,
            evidence=[], raw_data=None, confidence=1.5, error=None
        )
