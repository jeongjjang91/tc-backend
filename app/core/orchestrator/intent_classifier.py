from __future__ import annotations

import math
import re
from dataclasses import dataclass


INTENT_LABELS = ("db", "doc", "log", "knowledge")


@dataclass(frozen=True)
class IntentPrediction:
    label: str
    score: float
    margin: float
    entropy: float
    scores: dict[str, float]


class KeywordIntentClassifier:
    """Lightweight deterministic classifier for the T19 planner tier.

    This is intentionally small: it gives the planner a confidence/margin
    signal now, while leaving the same interface ready for an embedding or
    learned classifier later.
    """

    def __init__(self, seed_data: dict | None = None) -> None:
        data = seed_data or {}
        self._keywords: dict[str, list[str]] = {
            label: [str(v).lower() for v in data.get(label, [])]
            for label in INTENT_LABELS
        }
        if not any(self._keywords.values()):
            self._keywords = {
                "db": [
                    "조회",
                    "목록",
                    "건수",
                    "비교",
                    "평균",
                    "최대",
                    "최소",
                    "select",
                    "eqp",
                    "param",
                    "model_info",
                    "table",
                ],
                "doc": ["설명", "기능", "뭐야", "무엇", "사용법", "매뉴얼", "동작 원리", "what is"],
                "log": ["로그", "에러", "오류", "장애", "이상", "원인", "실패", "exception", "error"],
                "knowledge": ["faq", "지식", "운영 방법", "가이드", "인수인계", "베스트 프랙티스"],
            }

    def predict(self, message: str) -> IntentPrediction:
        text = message.lower()
        raw_scores = {label: 0.1 for label in INTENT_LABELS}

        for label, keywords in self._keywords.items():
            for keyword in keywords:
                if not keyword:
                    continue
                if keyword in text:
                    raw_scores[label] += 1.0 + min(len(keyword), 12) / 24

        if re.search(r"\bselect\b|\bfrom\b|\bwhere\b", text):
            raw_scores["db"] += 1.5
        if re.search(r"\b(error|exception|timeout|fail(?:ed|ure)?)\b", text):
            raw_scores["log"] += 1.2
        if "?" in text or "？" in text:
            raw_scores["doc"] += 0.1

        total = sum(raw_scores.values())
        probs = {label: score / total for label, score in raw_scores.items()}
        ranked = sorted(probs.items(), key=lambda item: item[1], reverse=True)
        label, score = ranked[0]
        margin = score - ranked[1][1]
        entropy = -sum(p * math.log(p + 1e-12) for p in probs.values())
        return IntentPrediction(label=label, score=score, margin=margin, entropy=entropy, scores=probs)
