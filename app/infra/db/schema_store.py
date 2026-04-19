from __future__ import annotations
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class SchemaStore:
    def __init__(self):
        self._schema: dict = {}
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        self._table_names: list[str] = []

    def load(self, schema: dict) -> None:
        self._schema = schema
        tables = schema.get("tables", {})
        self._table_names = list(tables.keys())

        docs = []
        for name, tconf in tables.items():
            col_texts = " ".join(
                f"{cn} {cd.get('description', '')} {cd.get('glossary_hint', '')}"
                for cn, cd in tconf.get("columns", {}).items()
            )
            doc = f"{name} {tconf.get('description', '')} {col_texts}"
            docs.append(doc)

        self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        self._matrix = self._vectorizer.fit_transform(docs)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if self._vectorizer is None:
            return []
        q_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(q_vec, self._matrix).flatten()
        top_idx = np.argsort(scores)[::-1][:top_k]
        tables = self._schema.get("tables", {})
        results = []
        for idx in top_idx:
            name = self._table_names[idx]
            results.append({"table": name, "score": float(scores[idx]), "config": tables[name]})
        return results

    def format_for_prompt(self, results: list[dict]) -> str:
        lines = []
        for r in results:
            name = r["table"]
            conf = r["config"]
            lines.append(f"테이블: {name} — {conf.get('description', '')}")
            for col, cconf in conf.get("columns", {}).items():
                hint = cconf.get("glossary_hint", "")
                lines.append(f"  - {col} ({cconf.get('type','')}) : {cconf.get('description','')} {hint}".strip())
            for rel in conf.get("relationships", []):
                lines.append(f"  관계: {rel}")
        return "\n".join(lines)
