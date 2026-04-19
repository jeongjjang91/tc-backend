from __future__ import annotations
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.core.agents.db.schema_linker import SchemaLinker
from app.core.agents.db.sql_generator import SQLGenerator
from app.core.agents.db.validator import SQLValidator
from app.core.agents.db.refiner import SQLRefiner
from app.core.agents.db.interpreter import ResultInterpreter
from app.infra.db.oracle import OraclePool
from app.infra.db.schema_store import SchemaStore
from app.infra.db.few_shot_store import FewShotStore
from app.shared.schemas import SubQuery, AgentResult, Evidence, Context
from app.shared.exceptions import SQLValidationError, DBExecutionError
from app.shared.logging import get_logger

logger = get_logger(__name__)


@register
class DBAgent(Agent):
    name = "db"

    def __init__(
        self,
        linker: SchemaLinker,
        generator: SQLGenerator,
        validator: SQLValidator,
        refiner: SQLRefiner,
        interpreter: ResultInterpreter,
        tc_pool: OraclePool,
        few_shot_store: FewShotStore,
        schema_store: SchemaStore,
        max_refine: int = 2,
        confidence_threshold: float = 0.7,
    ):
        self.linker = linker
        self.generator = generator
        self.validator = validator
        self.refiner = refiner
        self.interpreter = interpreter
        self.tc_pool = tc_pool
        self.few_shot_store = few_shot_store
        self.schema_store = schema_store
        self.max_refine = max_refine
        self.confidence_threshold = confidence_threshold

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        question = sub_query.query
        log = logger.bind(trace_id=context.trace_id, agent="db")

        # 1. Schema Linking
        log.info("schema_linking_start")
        linked = await self.linker.link(question)
        results = self.schema_store.search(question, top_k=5)
        schema_subset = self.schema_store.format_for_prompt(
            [r for r in results if r["table"] in linked.get("tables", [])] or results[:3]
        )

        # 2~5. SQL 생성 + 검증 + 실행 (refine loop)
        sql_result = await self.generator.generate(question, schema_subset, linked)
        sql = sql_result.get("sql", "")
        gen_confidence = sql_result.get("confidence", 0.0)
        rows: list[dict] = []

        for attempt in range(self.max_refine + 1):
            try:
                validated_sql = self.validator.validate_and_fix(sql)
                rows = await self.tc_pool.fetch_all(validated_sql)

                if not rows and attempt < self.max_refine:
                    log.info("empty_result_refine", attempt=attempt)
                    refined = await self.refiner.refine(
                        question, sql, "empty_result", "결과 0건", list(self.validator.allowed_tables)
                    )
                    sql = refined.get("sql", sql)
                    continue
                break

            except SQLValidationError as e:
                if attempt >= self.max_refine:
                    return AgentResult(
                        sub_query_id=sub_query.id,
                        success=False,
                        evidence=[],
                        raw_data=None,
                        confidence=0.0,
                        error=str(e),
                    )
                refined = await self.refiner.refine(
                    question, sql, "validation_error", str(e), list(self.validator.allowed_tables)
                )
                sql = refined.get("sql", sql)

            except DBExecutionError as e:
                if attempt >= self.max_refine:
                    return AgentResult(
                        sub_query_id=sub_query.id,
                        success=False,
                        evidence=[],
                        raw_data=None,
                        confidence=0.0,
                        error=str(e),
                    )
                refined = await self.refiner.refine(
                    question, sql, "syntax_error", str(e), list(self.validator.allowed_tables)
                )
                sql = refined.get("sql", sql)

        # 9. Result Interpretation
        interp = await self.interpreter.interpret(question, sql, rows)
        answer = interp.get("answer", "")
        confidence = min(gen_confidence, interp.get("confidence", gen_confidence))

        evidences = [
            Evidence(
                id=f"row_{i+1}",
                source_type="db_row",
                content=str(row),
                metadata={"sql": sql, "row_index": i},
            )
            for i, row in enumerate(rows)
        ]

        # 10. Success Cache
        if confidence >= self.confidence_threshold and rows:
            self.few_shot_store.add_success(question, sql)
            log.info("few_shot_cached", skeleton=question[:50])

        return AgentResult(
            sub_query_id=sub_query.id,
            success=True,
            evidence=evidences,
            raw_data={"sql": sql, "rows": rows, "answer": answer},
            confidence=confidence,
        )
