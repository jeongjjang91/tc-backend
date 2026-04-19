import pytest
from app.infra.db.schema_store import SchemaStore

SAMPLE_SCHEMA = {
    "tables": {
        "PARAMETER": {
            "description": "설비별 파라미터 정의 마스터",
            "columns": {
                "param_name": {"type": "VARCHAR2", "description": "파라미터 명칭"},
                "eqp_id": {"type": "VARCHAR2", "description": "설비 ID"},
            },
            "relationships": ["PARAMETER.eqp_id = MODEL_INFO.eqp_id"],
        },
        "MODEL_INFO": {
            "description": "설비 모델 정보 테이블",
            "columns": {
                "eqp_id": {"type": "VARCHAR2", "description": "설비 ID"},
                "model_name": {"type": "VARCHAR2", "description": "모델명"},
            },
            "relationships": [],
        },
    }
}


def test_search_returns_relevant_tables():
    store = SchemaStore()
    store.load(SAMPLE_SCHEMA)
    results = store.search("설비 파라미터 기능", top_k=2)
    table_names = [r["table"] for r in results]
    assert "PARAMETER" in table_names


def test_search_top_k_limits_results():
    store = SchemaStore()
    store.load(SAMPLE_SCHEMA)
    results = store.search("설비", top_k=1)
    assert len(results) == 1


def test_format_for_prompt():
    store = SchemaStore()
    store.load(SAMPLE_SCHEMA)
    results = store.search("파라미터", top_k=1)
    formatted = store.format_for_prompt(results)
    assert "PARAMETER" in formatted
    assert "param_name" in formatted
