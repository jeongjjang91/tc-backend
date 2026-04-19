from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer


REFINEMENT_HINTS = {
    "syntax_error": "Oracle SQL 문법 오류를 수정하세요. 특히 Oracle 고유 문법(ROWNUM, NVL, TO_DATE 등)을 확인하세요.",
    "empty_result": "WHERE 조건이 너무 좁을 수 있습니다. 조건을 완화하거나 LIKE 패턴을 사용해보세요.",
    "too_many_rows": "결과가 너무 많습니다. GROUP BY 또는 추가 WHERE 조건으로 집계하세요.",
    "validation_error": "허용되지 않은 테이블 또는 컬럼을 사용했습니다. 허용 목록만 사용하세요.",
}


class SQLRefiner:
    def __init__(self, llm: LLMProvider, renderer: PromptRenderer, max_attempts: int = 2):
        self.llm = llm
        self.renderer = renderer
        self.max_attempts = max_attempts

    async def refine(
        self,
        question: str,
        previous_sql: str,
        error_type: str,
        error_message: str,
        allowed_tables: list[str],
    ) -> dict:
        hint = REFINEMENT_HINTS.get(error_type, "오류를 분석하고 SQL을 수정하세요.")
        prompt = self.renderer.render(
            "sql_refiner",
            question=question,
            previous_sql=previous_sql,
            error_type=error_type,
            error_message=error_message,
            refinement_hint=hint,
            allowed_tables=", ".join(allowed_tables),
        )
        return await self.llm.complete_json(prompt)
