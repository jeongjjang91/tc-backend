from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.orchestrator.intent_classifier import KeywordIntentClassifier
from app.shared.logging import get_logger
from app.shared.schemas import SubQuery

logger = get_logger(__name__)

VALID_AGENTS = {"db", "doc", "log", "knowledge", "smalltalk"}

_DOC_PATTERNS = [
    r"설명",
    r"기능",
    r"뭐야",
    r"무엇",
    r"어떻게\s*동작",
    r"사용법",
    r"매뉴얼",
    r"what is",
    r"湲곕뒫",
    r"萸먯빞",
    r"ㅻ챸",
]
_LOG_PATTERNS = [
    r"로그",
    r"에러",
    r"오류",
    r"장애",
    r"이상",
    r"문제",
    r"원인",
    r"실패",
    r"error",
    r"먯씤",
    r"ㅻ룞",
    r"ㅻ쪟",
    r"먮윭",
    r"μ븷",
]
_KNOWLEDGE_PATTERNS = [r"faq", r"지식", r"운영\s*방법", r"가이드", r"베스트\s*프랙티스", r"명븯"]
_SMALLTALK_PATTERNS = [r"^안녕[하세요하십니까]*[.!?]?$", r"^hi[.!]?$", r"^hello[.!]?$", r"고마워", r"감사"]


@dataclass(frozen=True)
class PlannerThresholds:
    confidence: float = 0.55
    margin: float = 0.15
    entropy: float = 1.25


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
    return "knowledge"


def prefilter(message: str) -> str | None:
    msg = message.strip().lower()
    if not msg:
        return "smalltalk"
    if len(msg) <= 30:
        for pat in _SMALLTALK_PATTERNS:
            if re.search(pat, msg):
                return "smalltalk"
    return None


class QueryPlanner:
    """T18~T19 planner: prefilter -> classifier -> LLM fallback/decompose."""

    def __init__(
        self,
        llm=None,
        renderer=None,
        intent_classifier: KeywordIntentClassifier | None = None,
        thresholds: PlannerThresholds | None = None,
    ) -> None:
        self._llm = llm
        self._renderer = renderer
        self.intent_classifier = intent_classifier or KeywordIntentClassifier()
        self.thresholds = thresholds or PlannerThresholds()

    async def plan_async(
        self, message: str, session_id: str, history: list[dict] | None = None
    ) -> list[SubQuery]:
        pre = prefilter(message)
        if pre:
            logger.info("planner_prefilter", agent=pre, message=message[:50])
            return [self._sub_query(pre, message)]

        prediction = self.intent_classifier.predict(message)
        needs_fallback = (
            prediction.score < self.thresholds.confidence
            or prediction.margin < self.thresholds.margin
            or prediction.entropy > self.thresholds.entropy
        )

        if needs_fallback and self._llm and self._renderer:
            planned = await self._llm_plan(message, history=history or [])
            if planned:
                return planned
            rule_agent = _classify_rule(message)
            logger.info("planner_rule_after_llm_failure", agent=rule_agent, message=message[:50])
            return [self._sub_query(rule_agent, message)]

        logger.info(
            "planner_classifier",
            agent=prediction.label,
            score=round(prediction.score, 3),
            margin=round(prediction.margin, 3),
            entropy=round(prediction.entropy, 3),
            fallback=needs_fallback,
            message=message[:50],
        )
        return [self._sub_query(prediction.label, message)]

    def plan(self, message: str, session_id: str) -> list[SubQuery]:
        """Sync fallback for backward compatibility."""
        pre = prefilter(message)
        if pre:
            return [self._sub_query(pre, message)]
        agent = _classify_rule(message)
        logger.info("query_classified", agent=agent, message=message[:50])
        return [self._sub_query(agent, message)]

    async def _llm_plan(self, message: str, history: list[dict]) -> list[SubQuery] | None:
        try:
            prompt = self._renderer.render("planner", question=message, history=history)
            result = await self._llm.complete_json(prompt)
            sub_queries = self._parse_sub_queries(result, message)
            logger.info("planner_llm", count=len(sub_queries), message=message[:50])
            return sub_queries
        except Exception as exc:
            logger.warning("planner_llm_fallback_failed", error=str(exc))
            return None

    def _parse_sub_queries(self, result: dict[str, Any], message: str) -> list[SubQuery]:
        if isinstance(result.get("sub_queries"), list):
            queries = []
            for item in result["sub_queries"]:
                agent = str(item.get("agent", "knowledge"))
                if agent not in VALID_AGENTS:
                    agent = "knowledge"
                queries.append(self._sub_query(agent, str(item.get("query", message))))
            if queries:
                return queries

        agent = str(result.get("agent", "knowledge"))
        if agent not in VALID_AGENTS:
            agent = "knowledge"
        return [self._sub_query(agent, message)]

    @staticmethod
    def _sub_query(agent: str, query: str) -> SubQuery:
        return SubQuery(id=str(uuid.uuid4()), agent=agent, query=query)
