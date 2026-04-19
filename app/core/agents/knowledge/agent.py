from __future__ import annotations
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.infra.db.knowledge_repo import KnowledgeRepository
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, AgentResult, Evidence, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


@register
class KnowledgeAgent(Agent):
    name = "knowledge"

    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        knowledge_repo: KnowledgeRepository,
        top_k: int = 5,
    ):
        self.llm = llm
        self.renderer = renderer
        self.knowledge_repo = knowledge_repo
        self.top_k = top_k

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        question = sub_query.query
        log = logger.bind(trace_id=context.trace_id, agent="knowledge")

        log.info("knowledge_search_start")
        items = await self.knowledge_repo.search(question, limit=self.top_k)

        if not items:
            return AgentResult(
                sub_query_id=sub_query.id,
                success=True,
                evidence=[],
                raw_data={"answer": "지식베이스에서 관련 항목을 찾지 못했습니다.", "items": []},
                confidence=0.0,
            )

        answer_result = await self.llm.complete_json(
            self.renderer.render("knowledge_answer", question=question, items=items)
        )
        answer = answer_result.get("answer", "")
        confidence = float(answer_result.get("confidence", 0.0))

        evidences = [
            Evidence(
                id=f"kb_{i + 1}",
                source_type="knowledge_entry",
                content=item["content"][:500],
                metadata={
                    "item_id": item["item_id"],
                    "title": item["title"],
                    "category": item.get("category", ""),
                    "source": item.get("source", ""),
                },
            )
            for i, item in enumerate(items)
        ]

        log.info("knowledge_complete", items=len(items), confidence=confidence)
        return AgentResult(
            sub_query_id=sub_query.id,
            success=True,
            evidence=evidences,
            raw_data={"answer": answer, "items": items},
            confidence=confidence,
        )
