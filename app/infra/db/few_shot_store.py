from __future__ import annotations
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np


def extract_skeleton(
    question: str,
    known_eqps: list[str] | None = None,
    known_params: list[str] | None = None,
) -> str:
    s = question
    for v in (known_eqps or []):
        s = s.replace(v, "<EQP>")
    for v in (known_params or []):
        s = s.replace(v, "<PARAM>")
    s = re.sub(r"EQP_[A-Z0-9_]+", "<EQP>", s)
    s = re.sub(r"[A-Za-z]\s*설비", "<EQP> 설비", s)
    s = re.sub(r"PARAM_[A-Z0-9_]+", "<PARAM>", s)
    s = re.sub(r"DCOL_[A-Z0-9_]+", "<DCOL>", s)
    return s


class FewShotStore:
    def __init__(self):
        self._examples: list[dict] = []
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None

    def add_seed(self, examples: list[dict]) -> None:
        self._examples.extend(examples)
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        if not self._examples:
            return
        docs = [extract_skeleton(e["question"]) for e in self._examples]
        self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        self._matrix = self._vectorizer.fit_transform(docs)

    def search(self, question: str, top_k: int = 3) -> list[dict]:
        if self._vectorizer is None or not self._examples:
            return []
        skeleton = extract_skeleton(question)
        q_vec = self._vectorizer.transform([skeleton])
        scores = cosine_similarity(q_vec, self._matrix).flatten()
        top_idx = np.argsort(scores)[::-1][:top_k]
        return [self._examples[i] for i in top_idx if scores[i] > 0]

    def add_success(self, question: str, sql: str) -> None:
        example = {"question": question, "sql": sql, "source": "auto"}
        self._examples.append(example)
        self._rebuild_index()
