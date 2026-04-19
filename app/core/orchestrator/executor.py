from __future__ import annotations
import asyncio
from app.shared.schemas import SubQuery, AgentResult, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


class QueryExecutor:
    def __init__(self, agent_instances: dict) -> None:
        self._agents = agent_instances

    async def execute(self, sub_queries: list[SubQuery], context: Context) -> list[AgentResult]:
        tasks = []
        for sq in sub_queries:
            agent = self._agents.get(sq.agent)
            if not agent:
                logger.warning("agent_not_found", agent=sq.agent)
                tasks.append(self._error_result(sq, f"Agent '{sq.agent}' not found"))
                continue
            tasks.append(self._run_safe(agent, sq, context))
        return list(await asyncio.gather(*tasks))

    @staticmethod
    async def _run_safe(agent, sq: SubQuery, context: Context) -> AgentResult:
        try:
            return await agent.run(sq, context)
        except Exception as exc:
            logger.error("agent_run_error", agent=sq.agent, error=str(exc))
            return AgentResult(
                sub_query_id=sq.id,
                success=False,
                evidence=[],
                raw_data={"answer": "처리 중 오류가 발생했습니다."},
                confidence=0.0,
                error=str(exc),
            )

    @staticmethod
    async def _error_result(sq: SubQuery, message: str) -> AgentResult:
        return AgentResult(
            sub_query_id=sq.id,
            success=False,
            evidence=[],
            raw_data={"answer": message},
            confidence=0.0,
            error=message,
        )
