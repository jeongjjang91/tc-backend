from app.config import get_settings
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.mysql import MySQLPool
from app.infra.db.schema_store import SchemaStore
from app.infra.db.value_store import ValueStore
from app.infra.db.few_shot_store import FewShotStore
from app.infra.db.sessions import SessionRepository
from app.infra.db.review_repo import ReviewRepository
from app.infra.rag.confluence_client import ConfluenceClient
from app.infra.rag.reranker import TFIDFReranker
from app.infra.splunk.client import SplunkClient
from app.infra.config.loader import ConfigLoader
from app.core.agents.db.schema_linker import SchemaLinker
from app.core.agents.db.sql_generator import SQLGenerator
from app.core.agents.db.validator import SQLValidator
from app.core.agents.db.refiner import SQLRefiner
from app.core.agents.db.interpreter import ResultInterpreter
from app.core.agents.db.agent import DBAgent
from app.core.agents.rag.agent import RAGAgent
from app.core.agents.log.agent import SplunkAgent
from app.core.agents.knowledge.agent import KnowledgeAgent
from app.infra.db.knowledge_repo import KnowledgeRepository
from app.core.orchestrator.planner import QueryPlanner
from app.core.orchestrator.executor import QueryExecutor

_db_agent: DBAgent | None = None
_rag_agent: RAGAgent | None = None
_splunk_agent: SplunkAgent | None = None
_session_repo: SessionRepository | None = None
_review_repo: ReviewRepository | None = None
_planner: QueryPlanner | None = None
_executor: QueryExecutor | None = None


async def get_db_agent() -> DBAgent:
    return _db_agent


async def get_rag_agent() -> RAGAgent:
    return _rag_agent


async def get_splunk_agent() -> SplunkAgent:
    return _splunk_agent


async def get_session_repo() -> SessionRepository:
    return _session_repo


async def get_review_repo() -> ReviewRepository:
    return _review_repo


async def get_planner() -> QueryPlanner:
    return _planner


async def get_executor() -> QueryExecutor:
    return _executor


async def init_dependencies() -> None:
    global _db_agent, _rag_agent, _splunk_agent, _session_repo, _review_repo, _planner, _executor
    s = get_settings()
    loader = ConfigLoader(s.config_dir)
    thresholds = loader.load_thresholds()
    whitelist = loader.load_whitelist()
    schema_data = loader.load_schema()
    seed_data = loader.load_few_shot_seed()

    llm = InternalLLMProvider(s.llm_api_base_url, s.llm_api_key, s.llm_model)
    renderer = PromptRenderer(f"{s.config_dir}/prompts")

    schema_store = SchemaStore()
    schema_store.load(schema_data)

    value_store = ValueStore()
    few_shot_store = FewShotStore()
    few_shot_store.add_seed(seed_data)

    validator = SQLValidator(whitelist=whitelist)

    tc_pool = MySQLPool(
        host=s.tc_db_host, port=s.tc_db_port, db=s.tc_db_name,
        user=s.tc_db_user, password=s.tc_db_password,
    )
    await tc_pool.start()

    app_pool = MySQLPool(
        host=s.app_db_host, port=s.app_db_port, db=s.app_db_name,
        user=s.app_db_user, password=s.app_db_password,
    )
    await app_pool.start()

    _session_repo = SessionRepository(app_pool)
    _review_repo = ReviewRepository(app_pool)

    _db_agent = DBAgent(
        linker=SchemaLinker(llm, renderer, schema_store, thresholds.get("schema_rag_top_k", 5)),
        generator=SQLGenerator(llm, renderer, few_shot_store, value_store, thresholds.get("few_shot_top_k", 3)),
        validator=validator,
        refiner=SQLRefiner(llm, renderer, thresholds.get("max_refine_attempts", 2)),
        interpreter=ResultInterpreter(llm, renderer),
        tc_pool=tc_pool,
        few_shot_store=few_shot_store,
        schema_store=schema_store,
        max_refine=thresholds.get("max_refine_attempts", 2),
        confidence_threshold=thresholds.get("confidence_auto_send", 0.7),
    )

    confluence = ConfluenceClient(
        base_url=s.confluence_base_url,
        token=s.confluence_token,
        space_key=s.confluence_space_key,
    )
    _rag_agent = RAGAgent(
        llm=llm,
        renderer=renderer,
        confluence=confluence,
        reranker=TFIDFReranker(),
        top_k=thresholds.get("rag_top_k", 5),
    )

    splunk = SplunkClient(
        host=s.splunk_host,
        port=s.splunk_port,
        token=s.splunk_token,
        index=s.splunk_index,
    )
    _splunk_agent = SplunkAgent(
        llm=llm,
        renderer=renderer,
        splunk=splunk,
        review_repo=_review_repo,
        splunk_index=s.splunk_index,
        review_threshold=thresholds.get("review_threshold", 0.6),
    )

    knowledge_repo = KnowledgeRepository(app_pool)
    _knowledge_agent = KnowledgeAgent(
        llm=llm,
        renderer=renderer,
        knowledge_repo=knowledge_repo,
        top_k=thresholds.get("knowledge_top_k", 5),
    )

    _planner = QueryPlanner(llm=llm, renderer=renderer)
    _executor = QueryExecutor(
        agent_instances={"db": _db_agent, "doc": _rag_agent, "log": _splunk_agent, "knowledge": _knowledge_agent}
    )
