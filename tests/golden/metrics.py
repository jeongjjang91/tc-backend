import re
from dataclasses import dataclass, field


@dataclass
class EvalResult:
    id: str
    difficulty: str
    passed: bool
    score: float
    failures: list[str] = field(default_factory=list)


def evaluate(case: dict, actual_sql: str, actual_answer: str) -> EvalResult:
    expected = case.get("expected", {})
    failures = []
    scores = []

    sql_upper = actual_sql.upper()

    for col in expected.get("sql_must_filter_on", []):
        if col.upper() not in sql_upper:
            failures.append(f"SQL이 '{col}' 컬럼을 필터링하지 않음")
        scores.append(col.upper() in sql_upper)

    for table in [expected.get("sql_must_use_table", "")]:
        if table and table.upper() not in sql_upper:
            failures.append(f"SQL이 '{table}' 테이블을 사용하지 않음")
        if table:
            scores.append(table.upper() in sql_upper)

    for keyword in expected.get("sql_must_contain", []):
        found = any(k.upper() in sql_upper for k in keyword.split(","))
        if not found:
            failures.append(f"SQL에 '{keyword}' 없음")
        scores.append(found)

    for term in expected.get("answer_must_contain", []):
        if term.lower() not in actual_answer.lower():
            failures.append(f"답변에 '{term}' 없음")
        scores.append(term.lower() in actual_answer.lower())

    if expected.get("citation_required"):
        has_citation = bool(re.search(r"\[row_\d+\]", actual_answer))
        if not has_citation:
            failures.append("답변에 인용([row_N]) 없음")
        scores.append(has_citation)

    overall = sum(scores) / len(scores) if scores else 0.0
    return EvalResult(
        id=case["id"],
        difficulty=case.get("difficulty", "unknown"),
        passed=len(failures) == 0,
        score=overall,
        failures=failures,
    )
