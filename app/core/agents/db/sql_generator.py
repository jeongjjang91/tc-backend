from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.few_shot_store import FewShotStore
from app.infra.db.value_store import ValueStore


class SQLGenerator:
    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        few_shot_store: FewShotStore,
        value_store: ValueStore,
        few_shot_top_k: int = 3,
    ):
        self.llm = llm
        self.renderer = renderer
        self.few_shot_store = few_shot_store
        self.value_store = value_store
        self.few_shot_top_k = few_shot_top_k

    async def generate(self, question: str, schema_subset: str, linked: dict) -> dict:
        few_shots = self.few_shot_store.search(question, top_k=self.few_shot_top_k)
        value_candidates = self.value_store.extract_from_question(question)
        prompt = self.renderer.render(
            "sql_gen",
            schema_subset=schema_subset,
            question=question,
            few_shots=few_shots,
            value_candidates=value_candidates,
        )
        return await self.llm.complete_json(prompt)
