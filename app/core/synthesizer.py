from __future__ import annotations
from app.shared.schemas import AgentResult
from app.shared.logging import get_logger

logger = get_logger(__name__)


class Synthesizer:
    """Merges multiple AgentResult objects into a single answer string."""

    def __init__(self, llm=None, renderer=None) -> None:
        self._llm = llm
        self._renderer = renderer

    async def synthesize(
        self, question: str, results: list[AgentResult], trace_id: str = ""
    ) -> dict:
        if not results:
            return {"answer": "처리 결과가 없습니다.", "confidence": 0.0, "evidence": []}

        if len(results) == 1:
            r = results[0]
            answer = r.raw_data.get("answer", "") if r.raw_data else ""
            return {"answer": answer, "confidence": r.confidence, "evidence": r.evidence}

        if self._llm and self._renderer:
            try:
                result_payloads = [
                    {
                        "agent": r.sub_query_id,
                        "answer": r.raw_data.get("answer", "") if r.raw_data else "",
                        "confidence": r.confidence,
                    }
                    for r in results
                ]
                prompt = self._renderer.render(
                    "synthesizer_multi", question=question, results=result_payloads
                )
                merged = await self._llm.complete_json(prompt)
                all_evidence = [ev for r in results for ev in r.evidence]
                return {
                    "answer": merged.get("answer", ""),
                    "confidence": float(merged.get("confidence", 0.0)),
                    "evidence": all_evidence,
                }
            except Exception as exc:
                logger.warning("synthesizer_llm_error", error=str(exc))

        # Fallback: use highest-confidence result
        best = max(results, key=lambda r: r.confidence)
        answer = best.raw_data.get("answer", "") if best.raw_data else ""
        all_evidence = [ev for r in results for ev in r.evidence]
        return {"answer": answer, "confidence": best.confidence, "evidence": all_evidence}
