import pytest
import yaml
from pathlib import Path

DATASET = Path("tests/golden/datasets/db_phase1.yaml")


def test_golden_dataset_is_valid():
    with open(DATASET, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "examples" in data
    assert len(data["examples"]) >= 30
    for ex in data["examples"]:
        assert "id" in ex
        assert "question" in ex
        assert "expected" in ex
        assert "difficulty" in ex


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_golden_phase1_no_regression(db_agent):
    from tests.golden.runner import run_golden_eval
    report = await run_golden_eval(db_agent, str(DATASET))
    baseline = yaml.safe_load(open(DATASET, encoding="utf-8"))["baseline_score"]
    if baseline is not None:
        assert report["overall_score"] >= baseline - 0.05, (
            f"Regression! {report['overall_score']:.2f} vs baseline {baseline:.2f}\n"
            f"Failures: {report['failures']}"
        )
