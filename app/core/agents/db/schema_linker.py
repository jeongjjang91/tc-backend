from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.schema_store import SchemaStore


class SchemaLinker:
    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        schema_store: SchemaStore,
        top_k: int = 5,
    ):
        self.llm = llm
        self.renderer = renderer
        self.schema_store = schema_store
        self.top_k = top_k

    async def link(self, question: str) -> dict:
        results = self.schema_store.search(question, top_k=self.top_k)
        schema_context = self.schema_store.format_for_prompt(results)
        prompt = self.renderer.render("schema_linker", schema_context=schema_context, question=question)
        return await self.llm.complete_json(prompt)
