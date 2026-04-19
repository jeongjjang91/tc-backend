from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer


class ResultInterpreter:
    def __init__(self, llm: LLMProvider, renderer: PromptRenderer, max_rows_in_prompt: int = 20):
        self.llm = llm
        self.renderer = renderer
        self.max_rows_in_prompt = max_rows_in_prompt

    async def interpret(self, question: str, sql: str, rows: list[dict]) -> dict:
        prompt = self.renderer.render(
            "synthesizer",
            question=question,
            sql=sql,
            rows=rows[: self.max_rows_in_prompt],
            row_count=len(rows),
        )
        return await self.llm.complete_json(prompt)
