from __future__ import annotations
import re
import uuid
from app.shared.schemas import SubQuery
from app.shared.logging import get_logger

logger = get_logger(__name__)

# Phase 4에서 LLM 기반 분류로 업그레이드 예정
_DOC_PATTERNS = [
    r"설명", r"기능이 뭐", r"어떻게 동작", r"뭐야", r"무엇", r"what is",
]
_LOG_PATTERNS = [
    r"오동작", r"에러", r"오류", r"왜 안", r"문제", r"장애",
]


def classify_question(message: str) -> str:
    msg = message.lower()
    for pat in _LOG_PATTERNS:
        if re.search(pat, msg):
            return "log"
    for pat in _DOC_PATTERNS:
        if re.search(pat, msg):
            return "doc"
    return "db"


class QueryPlanner:
    def plan(self, message: str, session_id: str) -> list[SubQuery]:
        agent_name = classify_question(message)
        logger.info("query_classified", agent=agent_name, message=message[:50])
        return [SubQuery(id=str(uuid.uuid4()), agent=agent_name, query=message)]
