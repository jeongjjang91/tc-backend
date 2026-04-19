from __future__ import annotations
import asyncio
from app.core.agents.registry import AGENT_REGISTRY
from app.shared.schemas import SubQuery, AgentResult, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


class QueryExecutor:
    def __init__(self, agent_instances: dict):
        # {agent_name: Agent instance} — deps.py에서 주입
        self._agents = agent_instances

    async def execute(self, sub_queries: list[SubQuery], context: Context) -> list[AgentResult]:
        tasks = []
        for sq in sub_queries:
            agent = self._agents.get(sq.agent)
            if not agent:
                logger.warning("agent_not_found", agent=sq.agent)
                continue
            tasks.append(agent.run(sq, context))
        return list(await asyncio.gather(*tasks))
