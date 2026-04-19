from __future__ import annotations
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class TFIDFReranker:
    def rerank(self, query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
        if not chunks:
            return []
        top_k = min(top_k, len(chunks))
        docs = [f"{c['title']} {c['content']}" for c in chunks]
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        matrix = vectorizer.fit_transform(docs)
        q_vec = vectorizer.transform([query])
        scores = cosine_similarity(q_vec, matrix).flatten()
        top_idx = np.argsort(scores)[::-1][:top_k]
        return [chunks[i] for i in top_idx]
