from __future__ import annotations
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.infra.splunk.client import SplunkClient
from app.infra.splunk.pattern_analyzer import PatternAnalyzer
from app.infra.db.review_repo import ReviewRepository
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, AgentResult, Evidence, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


@register
class SplunkAgent(Agent):
    name = "log"

    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        splunk: SplunkClient,
        review_repo: ReviewRepository,
        splunk_index: str = "main",
        review_threshold: float = 0.6,
    ):
        self.llm = llm
        self.renderer = renderer
        self.splunk = splunk
        self.review_repo = review_repo
        self.splunk_index = splunk_index
        self.review_threshold = review_threshold
        self._analyzer = PatternAnalyzer()

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        question = sub_query.query
        log = logger.bind(trace_id=context.trace_id, agent="log")

        # 1. Splunk 쿼리 생성
        log.info("splunk_query_start")
        query_result = await self.llm.complete_json(
            self.renderer.render("splunk_query", question=question, index=self.splunk_index)
        )
        spl_query = query_result.get("query", f"index={self.splunk_index}")
        earliest = query_result.get("earliest", "-24h")
        latest = query_result.get("latest", "now")

        # 2. Splunk 검색
        events = await self.splunk.search(spl_query, earliest=earliest, latest=latest)
        log.info("splunk_events", count=len(events))

        if not events:
            return AgentResult(
                sub_query_id=sub_query.id,
                success=True,
                evidence=[],
                raw_data={"answer": "해당 기간에 관련 로그가 없습니다.", "events": []},
                confidence=0.0,
            )

        # 3. 패턴 분석
        patterns = self._analyzer.analyze(events)

        # 4. LLM 분석
        analysis = await self.llm.complete_json(
            self.renderer.render(
                "log_analysis",
                question=question,
                events=events,
                pattern_summary=patterns["summary"],
                error_codes=patterns["error_codes"],
                error_count=patterns["error_count"],
            )
        )
        answer = analysis.get("answer", "")
        confidence = float(analysis.get("confidence", 0.0))
        needs_review = analysis.get("needs_human_review", False) or confidence < self.review_threshold

        evidences = [
            Evidence(
                id=f"log_{i + 1}",
                source_type="log_line",
                content=ev.get("_raw", "")[:500],
                metadata={"time": ev.get("_time", ""), "host": ev.get("host", ""), "index": i},
            )
            for i, ev in enumerate(events[:10])
        ]

        # 5. 검토 필요 시 pending_reviews 등록
        if needs_review:
            await self.review_repo.create_pending(
                session_id=context.session_id,
                trace_id=context.trace_id,
                question=question,
                draft_answer=answer,
                log_context=patterns,
                confidence=confidence,
            )
            log.info("review_pending_created", confidence=confidence)

        log.info("splunk_complete", events=len(events), confidence=confidence, needs_review=needs_review)
        return AgentResult(
            sub_query_id=sub_query.id,
            success=True,
            evidence=evidences,
            raw_data={
                "answer": answer,
                "root_cause": analysis.get("root_cause", ""),
                "recommendation": analysis.get("recommendation", ""),
                "needs_human_review": needs_review,
                "patterns": patterns,
            },
            confidence=confidence,
        )
