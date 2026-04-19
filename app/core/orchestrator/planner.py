from __future__ import annotations
import re
import uuid
from app.shared.schemas import SubQuery
from app.shared.logging import get_logger

logger = get_logger(__name__)

# Fallback rule-based classification (used when LLM planner is not injected)
_DOC_PATTERNS = [
    r"설명", r"기능이 뭐", r"어떻게 동작", r"뭐야", r"무엇", r"what is",
]
_LOG_PATTERNS = [
    r"오동작", r"에러", r"오류", r"왜 안", r"문제", r"장애",
]
_KNOWLEDGE_PATTERNS = [
    r"노하우", r"팁", r"faq", r"운영 방법", r"베스트 프랙티스",
]


def _classify_rule(message: str) -> str:
    msg = message.lower()
    for pat in _LOG_PATTERNS:
        if re.search(pat, msg):
            return "log"
    for pat in _KNOWLEDGE_PATTERNS:
        if re.search(pat, msg):
            return "knowledge"
    for pat in _DOC_PATTERNS:
        if re.search(pat, msg):
            return "doc"
    return "db"


class QueryPlanner:
    """
    LLM 기반 플래너. llm/renderer 없이 생성하면 rule-based fallback 사용.
    Phase 4에서 LLM 주입.
    """

    def __init__(self, llm=None, renderer=None) -> None:
        self._llm = llm
        self._renderer = renderer

    async def plan_async(
        self, message: str, session_id: str, history: list[dict] | None = None
    ) -> list[SubQuery]:
        if self._llm and self._renderer:
            try:
                prompt = self._renderer.render("planner", question=message, history=history or [])
                result = await self._llm.complete_json(prompt)
                agent = result.get("agent", "db")
                if agent not in ("db", "doc", "log", "knowledge"):
                    agent = "db"
                logger.info("planner_llm", agent=agent, message=message[:50])
                return [SubQuery(id=str(uuid.uuid4()), agent=agent, query=message)]
            except Exception as exc:
                logger.warning("planner_llm_fallback", error=str(exc))

        agent = _classify_rule(message)
        logger.info("planner_rule", agent=agent, message=message[:50])
        return [SubQuery(id=str(uuid.uuid4()), agent=agent, query=message)]

    def plan(self, message: str, session_id: str) -> list[SubQuery]:
        """Sync fallback for backward-compat."""
        agent = _classify_rule(message)
        logger.info("query_classified", agent=agent, message=message[:50])
        return [SubQuery(id=str(uuid.uuid4()), agent=agent, query=message)]
