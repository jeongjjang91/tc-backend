import pytest
import respx
import httpx
from unittest.mock import AsyncMock
from app.core.agents.db.schema_linker import SchemaLinker
from app.core.agents.db.sql_generator import SQLGenerator
from app.core.agents.db.validator import SQLValidator
from app.core.agents.db.refiner import SQLRefiner
from app.core.agents.db.interpreter import ResultInterpreter
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.schema_store import SchemaStore
from app.infra.db.few_shot_store import FewShotStore
from app.infra.db.value_store import ValueStore

WHITELIST = {
    "tables": {
        "PARAMETER": {"columns": ["param_id", "param_name", "eqp_id"], "requires_where_clause": True}
    },
    "large_tables": [],
    "forbidden_functions": ["DBMS_"],
}

SAMPLE_SCHEMA = {
    "tables": {
        "PARAMETER": {
            "description": "설비 파라미터",
            "columns": {"param_name": {"type": "VARCHAR2", "description": "파라미터명"}, "eqp_id": {"type": "VARCHAR2", "description": "설비ID"}},
            "relationships": [],
        }
    }
}


@pytest.fixture
def llm():
    return InternalLLMProvider(base_url="http://mock-llm", api_key="key", model="test")


@pytest.fixture
def renderer(tmp_path):
    (tmp_path / "schema_linker.j2").write_text("schema: {{ schema_context }}\nq: {{ question }}", encoding="utf-8")
    (tmp_path / "sql_gen.j2").write_text("schema: {{ schema_subset }}\nq: {{ question }}", encoding="utf-8")
    (tmp_path / "synthesizer.j2").write_text("q: {{ question }}\nsql: {{ sql }}\nrows: {{ rows }}", encoding="utf-8")
    (tmp_path / "sql_refiner.j2").write_text("q: {{ question }}\nprev: {{ previous_sql }}\nerr: {{ error_message }}\ntables: {{ allowed_tables }}", encoding="utf-8")
    return PromptRenderer(prompt_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_schema_linker_returns_tables(llm, renderer):
    store = SchemaStore()
    store.load(SAMPLE_SCHEMA)
    linker = SchemaLinker(llm, renderer, store)

    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"tables":["PARAMETER"],"columns":["PARAMETER.param_name"],"joins":[]}'}}]},
            )
        )
        result = await linker.link("A 설비에 PARAM_X 있나?")

    assert "PARAMETER" in result["tables"]


@pytest.mark.asyncio
async def test_sql_generator_produces_sql(llm, renderer):
    fs = FewShotStore()
    vs = ValueStore()
    gen = SQLGenerator(llm, renderer, fs, vs)

    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"reasoning":"test","sql":"SELECT param_name FROM PARAMETER WHERE eqp_id=\'EQP_A_001\'","confidence":0.9,"assumptions":[]}'}}]},
            )
        )
        result = await gen.generate("A 설비 파라미터?", "PARAMETER...", {})

    assert "sql" in result
    assert "SELECT" in result["sql"].upper()


@pytest.mark.asyncio
async def test_validator_blocks_bad_sql(llm, renderer):
    from app.shared.exceptions import SQLValidationError
    v = SQLValidator(whitelist=WHITELIST)
    with pytest.raises(SQLValidationError):
        v.validate_and_fix("DELETE FROM PARAMETER")


@pytest.mark.asyncio
async def test_refiner_called_on_syntax_error(llm, renderer):
    refiner = SQLRefiner(llm, renderer)
    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"reasoning":"fixed","sql":"SELECT param_name FROM PARAMETER WHERE eqp_id=\'X\' AND ROWNUM<=1000","confidence":0.8}'}}]},
            )
        )
        result = await refiner.refine(
            question="A 설비?",
            previous_sql="SELCT * FOM PARAMETER",
            error_type="syntax_error",
            error_message="ORA-00923: FROM keyword not found",
            allowed_tables=["PARAMETER"],
        )
    assert "sql" in result


@pytest.mark.asyncio
async def test_db_agent_full_pipeline_success(llm, renderer):
    schema_store = SchemaStore()
    schema_store.load(SAMPLE_SCHEMA)
    few_shot = FewShotStore()
    value_store = ValueStore()
    linker = SchemaLinker(llm, renderer, schema_store)
    gen = SQLGenerator(llm, renderer, few_shot, value_store)
    validator = SQLValidator(whitelist=WHITELIST)
    refiner = SQLRefiner(llm, renderer)
    interpreter = ResultInterpreter(llm, renderer)

    mock_pool = AsyncMock()
    mock_pool.fetch_all = AsyncMock(return_value=[{"param_name": "PARAM_X", "eqp_id": "EQP_A_001"}])

    from app.core.agents.db.agent import DBAgent
    from app.shared.schemas import SubQuery, Context
    import uuid

    agent = DBAgent(
        linker=linker, generator=gen, validator=validator,
        refiner=refiner, interpreter=interpreter,
        tc_pool=mock_pool, few_shot_store=few_shot,
        schema_store=schema_store,
    )

    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            side_effect=[
                httpx.Response(200, json={"choices": [{"message": {"content": '{"tables":["PARAMETER"],"columns":["PARAMETER.param_name"],"joins":[]}'}}]}),
                httpx.Response(200, json={"choices": [{"message": {"content": '{"reasoning":"ok","sql":"SELECT param_name FROM PARAMETER WHERE eqp_id=\'EQP_A_001\' AND param_name=\'PARAM_X\'","confidence":0.9,"assumptions":[]}'}}]}),
                httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"EQP_A_001에 PARAM_X가 존재합니다[row_1]","confidence":0.9,"needs_human_review":false,"missing_info":[]}'}}]}),
            ]
        )
        result = await agent.run(
            SubQuery(id="t1", agent="db", query="A 설비에 PARAM_X 있나?"),
            Context(session_id="s1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert result.confidence > 0.5
    assert len(result.evidence) > 0
