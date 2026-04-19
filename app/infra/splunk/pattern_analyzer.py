from __future__ import annotations
import re
from collections import Counter


_ERROR_CODE_RE = re.compile(r"CODE=([A-Z0-9]+)")
_ERROR_LINE_RE = re.compile(r"\bERROR\b", re.IGNORECASE)


class PatternAnalyzer:
    def analyze(self, events: list[dict]) -> dict:
        if not events:
            return {"error_count": 0, "error_codes": [], "top_error": None, "summary": ""}

        error_count = 0
        code_counter: Counter = Counter()

        for ev in events:
            raw = ev.get("_raw", "")
            if _ERROR_LINE_RE.search(raw):
                error_count += 1
                for code in _ERROR_CODE_RE.findall(raw):
                    code_counter[code] += 1

        error_codes = list(code_counter.keys())
        top_error = code_counter.most_common(1)[0][0] if code_counter else None

        summary = self._build_summary(error_count, code_counter, top_error)

        return {
            "error_count": error_count,
            "error_codes": error_codes,
            "top_error": top_error,
            "summary": summary,
        }

    def _build_summary(
        self, error_count: int, code_counter: Counter, top_error: str | None
    ) -> str:
        if error_count == 0:
            return ""
        parts = [f"총 {error_count}건의 에러 발생."]
        if top_error:
            parts.append(f"가장 빈번한 에러: {top_error} ({code_counter[top_error]}회)")
        if len(code_counter) > 1:
            others = [f"{c}({n})" for c, n in code_counter.most_common()[1:4]]
            parts.append(f"기타: {', '.join(others)}")
        return " ".join(parts)
