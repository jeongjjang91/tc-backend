import asyncio
import yaml
from pathlib import Path
from tests.golden.metrics import EvalResult, evaluate


async def run_golden_eval(agent, dataset_path: str) -> dict:
    with open(dataset_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    examples = data.get("examples", [])
    results: list[EvalResult] = []

    from app.shared.schemas import SubQuery, Context
    import uuid

    for case in examples:
        try:
            sq = SubQuery(id=str(uuid.uuid4()), agent="db", query=case["question"])
            ctx = Context(session_id="golden", trace_id=str(uuid.uuid4()))
            agent_result = await agent.run(sq, ctx)

            actual_sql = ""
            actual_answer = ""
            if agent_result.raw_data:
                actual_sql = agent_result.raw_data.get("sql", "")
                actual_answer = agent_result.raw_data.get("answer", "")

            result = evaluate(case, actual_sql, actual_answer)
        except Exception as e:
            result = EvalResult(
                id=case["id"],
                difficulty=case.get("difficulty", "unknown"),
                passed=False,
                score=0.0,
                failures=[f"Exception: {e}"],
            )
        results.append(result)

    by_difficulty: dict[str, list[EvalResult]] = {}
    for r in results:
        by_difficulty.setdefault(r.difficulty, []).append(r)

    report = {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "overall_score": sum(r.score for r in results) / len(results) if results else 0,
        "by_difficulty": {
            diff: {
                "count": len(rs),
                "passed": sum(1 for r in rs if r.passed),
                "avg_score": sum(r.score for r in rs) / len(rs),
            }
            for diff, rs in by_difficulty.items()
        },
        "failures": [
            {"id": r.id, "failures": r.failures}
            for r in results
            if not r.passed
        ],
    }
    return report


if __name__ == "__main__":
    print("Golden eval runner — import this module and call run_golden_eval(agent, path)")
