from __future__ import annotations
import difflib
import re


def _trigrams(s: str) -> set[str]:
    s = s.lower()
    return {s[i:i+3] for i in range(len(s) - 2)}


class ValueStore:
    def __init__(self):
        self._index: dict[str, list[str]] = {}

    def load_values(self, column: str, values: list[str]) -> None:
        self._index[column] = list(values)

    def find_candidates(self, term: str, top_n: int = 5) -> list[str]:
        all_values = [v for vals in self._index.values() for v in vals]
        if not all_values:
            return []
        scored = difflib.get_close_matches(term, all_values, n=top_n, cutoff=0.2)
        if scored:
            return scored
        # trigram fallback
        term_tg = _trigrams(term)
        results: list[tuple[float, str]] = []
        for v in all_values:
            overlap = len(term_tg & _trigrams(v))
            if overlap > 0:
                results.append((overlap, v))
        results.sort(reverse=True)
        if results:
            return [v for _, v in results[:top_n]]
        # token substring fallback: split term into alphanumeric tokens and
        # check if any token appears inside a value (case-insensitive)
        alpha_tokens = re.findall(r"[A-Za-z0-9]+", term)
        if alpha_tokens:
            seen: set[str] = set()
            token_results: list[tuple[int, str]] = []
            for v in all_values:
                v_upper = v.upper()
                max_len = max(
                    len(t) for t in alpha_tokens if t.upper() in v_upper
                ) if any(t.upper() in v_upper for t in alpha_tokens) else 0
                if max_len > 0 and v not in seen:
                    seen.add(v)
                    token_results.append((max_len, v))
            token_results.sort(reverse=True)
            if token_results:
                return [v for _, v in token_results[:top_n]]
        return []

    def extract_from_question(self, question: str) -> dict[str, list[str]]:
        tokens = re.findall(r"[A-Z_0-9]{3,}|[가-힣]+", question.upper())
        result: dict[str, list[str]] = {}
        for token in tokens:
            candidates = self.find_candidates(token, top_n=3)
            if candidates:
                result[token] = candidates
        return result
