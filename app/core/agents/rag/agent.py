from __future__ import annotations
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.infra.rag.confluence_client import ConfluenceClient
from app.infra.rag.reranker import TFIDFReranker
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, AgentResult, Evidence, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


@register
class RAGAgent(Agent):
    name = "doc"

    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        confluence: ConfluenceClient,
        reranker: TFIDFReranker,
        top_k: int = 5,
    ):
        self.llm = llm
        self.renderer = renderer
        self.confluence = confluence
        self.reranker = reranker
        self.top_k = top_k

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        question = sub_query.query
        log = logger.bind(trace_id=context.trace_id, agent="doc")

        # 1. 검색 쿼리 생성
        log.info("rag_query_start")
        query_result = await self.llm.complete_json(
            self.renderer.render("rag_query", question=question)
        )
        search_query = query_result.get("query", question)

        # 2. Confluence 검색
        chunks = await self.confluence.search(search_query, limit=10)
        if not chunks:
            return AgentResult(
                sub_query_id=sub_query.id,
                success=True,
                evidence=[],
                raw_data={"answer": "문서에서 확인되지 않습니다", "chunks": []},
                confidence=0.0,
            )

        # 3. Rerank
        top_chunks = self.reranker.rerank(question, chunks, top_k=self.top_k)

        # 4. 답변 생성
        answer_result = await self.llm.complete_json(
            self.renderer.render("rag_answer", question=question, docs=top_chunks)
        )
        answer = answer_result.get("answer", "")
        confidence = float(answer_result.get("confidence", 0.0))

        evidences = [
            Evidence(
                id=f"doc_{i + 1}",
                source_type="doc_chunk",
                content=chunk["content"][:500],
                metadata={"title": chunk["title"], "url": chunk.get("url", ""), "doc_index": i},
            )
            for i, chunk in enumerate(top_chunks)
        ]

        log.info("rag_complete", chunks=len(top_chunks), confidence=confidence)
        return AgentResult(
            sub_query_id=sub_query.id,
            success=True,
            evidence=evidences,
            raw_data={"answer": answer, "chunks": top_chunks},
            confidence=confidence,
        )
