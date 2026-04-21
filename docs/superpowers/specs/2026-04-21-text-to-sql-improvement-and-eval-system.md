# Text-to-SQL 개선 + 평가 자동화 시스템 설계

- **작성일:** 2026-04-21
- **상태:** 승인됨 — 구현 대기
- **담당:** AI Agent (이 문서를 읽고 구현)
- **관련 문서:** `docs/AGENT_GUIDE.md`, `docs/superpowers/specs/2026-04-18-tc-voc-chatbot-design.md`

---

## 0. AI Agent를 위한 온보딩 지시

이 문서를 읽는 AI Agent에게:

1. **먼저 읽을 것:** `docs/AGENT_GUIDE.md` 전체 (특히 섹션 3, 5, 6, 16, 18)
2. **현재 상태:** Phase 1~4 코드 완성. 사내 연결 완료(LLM/App DB/TC DB). Text-to-SQL 동작 확인됨.
3. **이 문서의 목적:** 평가 자동화 + Text-to-SQL 품질 개선을 구현한다.
4. **구현 순서:** 반드시 아래 순서대로. 앞 태스크가 뒤 태스크의 기반이 됨.
5. **코딩 원칙:**
   - TDD: 테스트 먼저, 구현 나중
   - LLM 출력 검증은 계약(Contract) 기반 (정확한 텍스트 일치 금지)
   - 새 파일 만들기 전 기존 패턴 확인 (`app/infra/db/review_repo.py` 참고)
   - 프롬프트는 반드시 `config/prompts/*.j2`로 분리 (코드 내 하드코딩 금지)

---

## 1. 범위

### 이번에 구현할 것

| # | 태스크 | 유형 | 예상 소요 |
|---|--------|------|----------|
| T1 | 평가 결과 DB 자동 저장 (DDL + Repository + Runner 연동) | 신규 | 2~3h |
| T2 | Golden Eval conftest fixture 추가 (실제 Agent 연결) | 신규 | 1h |
| T3 | ValueStore TC DB 실제 값 로드 | 수정 | 30m |
| T4 | Few-shot seed YAML 확장 (실제 질문 패턴) | 수정 | 1h |
| T5 | `tc_schema.yaml` description 품질 개선 가이드라인 + 검증 스크립트 | 신규 | 1h |
| T6 | `sql_gen.j2` Chain-of-Thought + MySQL 문법 강화 | 수정 | 1h |
| T7 | Golden Eval 실행 + baseline 고정 | 실행 | 30m |

### 이번에 구현하지 않는 것

- Few-shot 유사 검색 개선 (T1~T7 완료 후 별도 스펙)
- Splunk 이상 탐지 (별도 스펙)
- 신뢰도 캘리브레이션 (운영 데이터 쌓인 후)

---

## 2. T1: 평가 결과 DB 자동 저장

### 2-1. 배경

현재 Golden Eval 실행 결과가 콘솔 출력에만 남음. 시간에 따른 개선 추이를 추적하려면 DB에 저장해야 함. 논문 작성 시 실험 결과 표의 근거 데이터가 됨.

### 2-2. DDL — `db/migrations/004_eval_tracking.sql`

**이 파일을 신규 생성할 것.**

```sql
-- db/migrations/004_eval_tracking.sql
-- Golden Eval 실행 결과 자동 저장용

CREATE TABLE eval_run (
  run_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  dataset       VARCHAR(100)   NOT NULL,          -- 'db_phase1', 'rag_phase2' 등
  git_sha       VARCHAR(40),                       -- 실행 시점 커밋 해시
  branch        VARCHAR(100),
  overall_score DECIMAL(5,4)   NOT NULL,           -- 0.0000 ~ 1.0000
  passed        INT            NOT NULL,
  total         INT            NOT NULL,
  baseline      DECIMAL(5,4),                      -- 실행 시점 baseline_score 값
  regression    TINYINT(1)     DEFAULT 0,          -- 1=회귀 발생
  llm_model     VARCHAR(100),                      -- 실행에 사용한 LLM 모델명
  duration_sec  DECIMAL(8,2),                      -- 전체 실행 시간(초)
  triggered_by  VARCHAR(50)    DEFAULT 'manual',   -- 'manual' | 'ci' | 'pre_commit'
  created_at    TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_eval_run_dataset (dataset, created_at),
  INDEX idx_eval_run_regression (regression, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE eval_case (
  case_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id        BIGINT         NOT NULL,
  case_key      VARCHAR(100)   NOT NULL,           -- 'db_001', 'rag_003' 등 YAML의 id
  difficulty    VARCHAR(20),                        -- 'easy' | 'medium' | 'hard'
  passed        TINYINT(1)     NOT NULL,
  score         DECIMAL(5,4)   NOT NULL,
  question      TEXT,
  generated_sql TEXT,
  generated_answer TEXT,
  failure_reasons JSON,                             -- ["SQL이 'TABLE' 없음", ...]
  latency_ms    INT,                               -- 이 케이스 실행 시간
  created_at    TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES eval_run(run_id) ON DELETE CASCADE,
  INDEX idx_eval_case_run (run_id),
  INDEX idx_eval_case_key (case_key, passed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**실행 명령:**
```bash
mysql -h 호스트 -u voc_app -p APPDB < db/migrations/004_eval_tracking.sql
```

### 2-3. EvalRepository — `app/infra/db/eval_repo.py`

**이 파일을 신규 생성할 것.** 기존 `review_repo.py` 패턴을 그대로 따를 것.

```python
# app/infra/db/eval_repo.py
from __future__ import annotations
from dataclasses import dataclass
from app.infra.db.base import DBPool


@dataclass
class EvalRunRecord:
    dataset: str
    overall_score: float
    passed: int
    total: int
    baseline: float | None
    regression: bool
    llm_model: str
    duration_sec: float
    git_sha: str = ""
    branch: str = ""
    triggered_by: str = "manual"


@dataclass
class EvalCaseRecord:
    run_id: int
    case_key: str
    difficulty: str
    passed: bool
    score: float
    question: str
    generated_sql: str
    generated_answer: str
    failure_reasons: list[str]
    latency_ms: int


class EvalRepository:
    def __init__(self, pool: DBPool):
        self._pool = pool

    async def create_run(self, record: EvalRunRecord) -> int:
        """eval_run 행 삽입 후 run_id 반환"""
        await self._pool.execute(
            """
            INSERT INTO eval_run
              (dataset, git_sha, branch, overall_score, passed, total,
               baseline, regression, llm_model, duration_sec, triggered_by)
            VALUES
              (%(dataset)s, %(git_sha)s, %(branch)s, %(overall_score)s,
               %(passed)s, %(total)s, %(baseline)s, %(regression)s,
               %(llm_model)s, %(duration_sec)s, %(triggered_by)s)
            """,
            record.__dict__,
        )
        rows = await self._pool.fetch_all("SELECT LAST_INSERT_ID() AS id")
        return rows[0]["id"]

    async def create_cases(self, cases: list[EvalCaseRecord]) -> None:
        """eval_case 행 일괄 삽입"""
        import json
        for c in cases:
            await self._pool.execute(
                """
                INSERT INTO eval_case
                  (run_id, case_key, difficulty, passed, score,
                   question, generated_sql, generated_answer,
                   failure_reasons, latency_ms)
                VALUES
                  (%(run_id)s, %(case_key)s, %(difficulty)s, %(passed)s,
                   %(score)s, %(question)s, %(generated_sql)s,
                   %(generated_answer)s, %(failure_reasons)s, %(latency_ms)s)
                """,
                {**c.__dict__, "failure_reasons": json.dumps(c.failure_reasons, ensure_ascii=False)},
            )

    async def get_last_run(self, dataset: str) -> dict | None:
        """해당 dataset의 가장 최근 run 반환"""
        rows = await self._pool.fetch_all(
            """
            SELECT * FROM eval_run
            WHERE dataset = %(dataset)s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"dataset": dataset},
        )
        return rows[0] if rows else None

    async def get_trend(self, dataset: str, limit: int = 20) -> list[dict]:
        """점수 추이 (최근 N회 실행)"""
        return await self._pool.fetch_all(
            """
            SELECT run_id, overall_score, passed, total, regression,
                   git_sha, created_at
            FROM eval_run
            WHERE dataset = %(dataset)s
            ORDER BY created_at DESC
            LIMIT %(limit)s
            """,
            {"dataset": dataset, "limit": limit},
        )
```

**주의:**
- `DBPool.execute()`는 INSERT/UPDATE 전용. `fetch_all()`은 SELECT 전용. 섞지 말 것.
- MySQL은 파라미터 바인딩이 `%(name)s` 형식 (Oracle의 `:name`과 다름).

### 2-4. Golden Eval Runner 수정 — `tests/golden/runner.py`

기존 `run_golden_eval()` 함수에 `eval_repo` 파라미터를 추가하고, 결과를 자동 저장하도록 수정.

**수정할 파일:** `tests/golden/runner.py`

변경 사항:
1. 함수 시그니처에 `eval_repo: EvalRepository | None = None` 추가
2. 케이스별 실행 시간 측정 (`time.monotonic()`)
3. `eval_repo`가 있으면 `create_run()` + `create_cases()` 호출
4. git 정보는 `subprocess.run(["git", "rev-parse", "HEAD"])` 로 추출

```python
# tests/golden/runner.py — 수정 후 전체 모습

import asyncio
import time
import subprocess
import yaml
from pathlib import Path
from tests.golden.metrics import EvalResult, evaluate
from app.infra.db.eval_repo import EvalRepository, EvalRunRecord, EvalCaseRecord


def _get_git_info() -> tuple[str, str]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True
        ).stdout.strip()
        return sha, branch
    except Exception:
        return "", ""


async def run_golden_eval(
    agent,
    dataset_path: str,
    eval_repo: EvalRepository | None = None,
    llm_model: str = "",
    triggered_by: str = "manual",
) -> dict:
    with open(dataset_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    examples = data.get("examples", [])
    baseline = data.get("baseline_score")
    results: list[EvalResult] = []
    case_records: list[EvalCaseRecord] = []

    from app.shared.schemas import SubQuery, Context
    import uuid

    run_start = time.monotonic()

    for case in examples:
        case_start = time.monotonic()
        actual_sql = ""
        actual_answer = ""

        try:
            sq = SubQuery(id=str(uuid.uuid4()), agent="db", query=case["question"])
            ctx = Context(session_id="golden", trace_id=str(uuid.uuid4()))
            agent_result = await agent.run(sq, ctx)

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

        latency_ms = int((time.monotonic() - case_start) * 1000)
        results.append(result)
        case_records.append(EvalCaseRecord(
            run_id=0,  # create_run() 후 채움
            case_key=result.id,
            difficulty=result.difficulty,
            passed=result.passed,
            score=result.score,
            question=case.get("question", ""),
            generated_sql=actual_sql,
            generated_answer=actual_answer,
            failure_reasons=result.failures,
            latency_ms=latency_ms,
        ))

    duration_sec = time.monotonic() - run_start

    # 집계
    by_difficulty: dict[str, list[EvalResult]] = {}
    for r in results:
        by_difficulty.setdefault(r.difficulty, []).append(r)

    overall_score = sum(r.score for r in results) / len(results) if results else 0.0
    passed_count = sum(1 for r in results if r.passed)
    regression = baseline is not None and overall_score < baseline - 0.05

    report = {
        "total": len(results),
        "passed": passed_count,
        "overall_score": overall_score,
        "baseline": baseline,
        "regression": regression,
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
            for r in results if not r.passed
        ],
    }

    # DB 저장
    if eval_repo is not None:
        sha, branch = _get_git_info()
        run_record = EvalRunRecord(
            dataset=Path(dataset_path).stem,
            overall_score=overall_score,
            passed=passed_count,
            total=len(results),
            baseline=baseline,
            regression=regression,
            llm_model=llm_model,
            duration_sec=round(duration_sec, 2),
            git_sha=sha,
            branch=branch,
            triggered_by=triggered_by,
        )
        run_id = await eval_repo.create_run(run_record)
        for c in case_records:
            c.run_id = run_id
        await eval_repo.create_cases(case_records)
        report["run_id"] = run_id

    return report
```

### 2-5. deps.py 수정

`init_dependencies()` 에 `EvalRepository` 추가:

```python
# app/api/deps.py — 추가할 내용

from app.infra.db.eval_repo import EvalRepository

_eval_repo: EvalRepository | None = None

async def get_eval_repo() -> EvalRepository:
    return _eval_repo

# init_dependencies() 내부에 추가:
_eval_repo = EvalRepository(app_pool)
```

### 2-6. 단위 테스트 — `tests/unit/test_eval_repo.py`

**이 파일을 신규 생성할 것.**

```python
# tests/unit/test_eval_repo.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.infra.db.eval_repo import EvalRepository, EvalRunRecord, EvalCaseRecord


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch_all = AsyncMock(return_value=[{"id": 42}])
    return pool


@pytest.fixture
def repo(mock_pool):
    return EvalRepository(mock_pool)


@pytest.mark.asyncio
async def test_create_run_returns_id(repo, mock_pool):
    record = EvalRunRecord(
        dataset="db_phase1", overall_score=0.85, passed=25, total=30,
        baseline=0.80, regression=False, llm_model="gpt-oss",
        duration_sec=120.5,
    )
    run_id = await repo.create_run(record)
    assert run_id == 42
    mock_pool.execute.assert_called_once()


@pytest.mark.asyncio
async def test_create_cases_inserts_all(repo, mock_pool):
    cases = [
        EvalCaseRecord(
            run_id=1, case_key="db_001", difficulty="easy",
            passed=True, score=1.0, question="Q", generated_sql="SELECT 1",
            generated_answer="A", failure_reasons=[], latency_ms=300,
        )
        for _ in range(3)
    ]
    await repo.create_cases(cases)
    assert mock_pool.execute.call_count == 3


@pytest.mark.asyncio
async def test_get_last_run_returns_none_when_empty(repo, mock_pool):
    mock_pool.fetch_all.return_value = []
    result = await repo.get_last_run("db_phase1")
    assert result is None
```

---

## 3. T2: conftest.py — Golden Eval 실제 Agent fixture

### 3-1. 배경

현재 `tests/golden/test_golden_regression.py`의 `test_golden_phase1_no_regression`이 `db_agent` fixture를 사용하는데, 이 fixture가 `tests/conftest.py`에 없어서 실행 불가. 실제 LLM + TC DB에 연결된 DBAgent를 반환하는 fixture가 필요.

### 3-2. `tests/conftest.py` 수정

기존 내용 유지하고 아래를 추가:

```python
# tests/conftest.py — 추가할 내용

import pytest
import pytest_asyncio
from app.config import get_settings
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.mysql import MySQLPool
from app.infra.db.schema_store import SchemaStore
from app.infra.db.value_store import ValueStore
from app.infra.db.few_shot_store import FewShotStore
from app.infra.config.loader import ConfigLoader
from app.core.agents.db.schema_linker import SchemaLinker
from app.core.agents.db.sql_generator import SQLGenerator
from app.core.agents.db.validator import SQLValidator
from app.core.agents.db.refiner import SQLRefiner
from app.core.agents.db.interpreter import ResultInterpreter
from app.core.agents.db.agent import DBAgent
from app.infra.db.eval_repo import EvalRepository


@pytest_asyncio.fixture(scope="session")
async def db_agent():
    """실제 LLM + TC DB 연결 DBAgent. real_llm 마크 테스트에서만 사용."""
    s = get_settings()
    loader = ConfigLoader(s.config_dir)

    llm = InternalLLMProvider(s.llm_api_base_url, s.llm_api_key, s.llm_model)
    renderer = PromptRenderer(f"{s.config_dir}/prompts")

    schema_store = SchemaStore()
    schema_store.load(loader.load_schema())

    value_store = ValueStore()
    few_shot_store = FewShotStore()
    few_shot_store.add_seed(loader.load_few_shot_seed())

    whitelist = loader.load_whitelist()
    validator = SQLValidator(whitelist=whitelist)

    tc_pool = MySQLPool(
        host=s.tc_db_host, port=s.tc_db_port, db=s.tc_db_name,
        user=s.tc_db_user, password=s.tc_db_password,
    )
    await tc_pool.start()

    agent = DBAgent(
        linker=SchemaLinker(llm, renderer, schema_store),
        generator=SQLGenerator(llm, renderer, few_shot_store, value_store),
        validator=validator,
        refiner=SQLRefiner(llm, renderer),
        interpreter=ResultInterpreter(llm, renderer),
        tc_pool=tc_pool,
        few_shot_store=few_shot_store,
        schema_store=schema_store,
    )

    yield agent

    await tc_pool.stop()


@pytest_asyncio.fixture(scope="session")
async def eval_repo():
    """실제 App DB EvalRepository. real_llm 테스트에서 결과 저장용."""
    s = get_settings()
    app_pool = MySQLPool(
        host=s.app_db_host, port=s.app_db_port, db=s.app_db_name,
        user=s.app_db_user, password=s.app_db_password,
    )
    await app_pool.start()
    repo = EvalRepository(app_pool)
    yield repo
    await app_pool.stop()
```

### 3-3. `test_golden_regression.py` 수정

`eval_repo` fixture를 받아 Runner에 전달하도록 수정:

```python
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_golden_phase1_no_regression(db_agent, eval_repo):
    from tests.golden.runner import run_golden_eval
    import os
    report = await run_golden_eval(
        db_agent,
        str(DATASET),
        eval_repo=eval_repo,
        llm_model=os.getenv("LLM_MODEL", "unknown"),
        triggered_by="manual",
    )
    baseline = yaml.safe_load(open(DATASET, encoding="utf-8"))["baseline_score"]
    if baseline is not None:
        assert report["overall_score"] >= baseline - 0.05, (
            f"Regression! {report['overall_score']:.2f} vs baseline {baseline:.2f}\n"
            f"Failures: {report['failures']}"
        )
    print(f"\n[Golden Eval] run_id={report.get('run_id')} score={report['overall_score']:.3f}")
```

---

## 4. T3: ValueStore 실제 값 로드

### 4-1. 배경

`app/infra/db/value_store.py`는 현재 기동 시 빈 상태. "A1 설비" → TC DB의 실제 EQPID 매핑이 안 됨. Schema Linker가 WHERE 조건 값을 추론할 수 없어 SQL 정확도 저하.

### 4-2. `app/api/deps.py` 수정

`init_dependencies()` 내 `tc_pool.start()` 다음에 추가:

```python
# app/api/deps.py — init_dependencies() 내부 tc_pool.start() 이후에 삽입

# ValueStore: TC DB 실제 값 로드
# 설비 ID/이름, 파라미터명을 미리 로드해서 SQL 생성 시 정확한 값 매핑
try:
    eqp_rows = await tc_pool.fetch_all(
        "SELECT DISTINCT EQPID FROM TC_EQUIPMENT LIMIT 5000"
    )
    param_rows = await tc_pool.fetch_all(
        "SELECT DISTINCT PARAM_NAME FROM TC_EQP_PARAM LIMIT 20000"
    )
    value_store.load_values({
        "EQPID": [r["EQPID"] for r in eqp_rows if r.get("EQPID")],
        "PARAM_NAME": [r["PARAM_NAME"] for r in param_rows if r.get("PARAM_NAME")],
    })
    import structlog
    structlog.get_logger().info(
        "value_store_loaded",
        eqp_count=len(eqp_rows),
        param_count=len(param_rows),
    )
except Exception as e:
    import structlog
    structlog.get_logger().warning("value_store_load_failed", error=str(e))
    # 로드 실패해도 서버 기동은 계속 (빈 상태로 동작)
```

**주의:**
- 실제 TC DB의 테이블명/컬럼명이 다를 수 있음. `tc_schema.yaml`과 일치시킬 것.
- 값이 10만 건 초과 시 `LIMIT` 줄이거나 샘플링.
- 로드 실패해도 서버 기동 중단 금지 (try/except 필수).

---

## 5. T4: Few-shot Seed 확장

### 5-1. 현재 상태

`config/few_shot/sql_seed.yaml`에 샘플 몇 개. 실제 운영자 질문 패턴 부재 → Few-shot 검색(`FewShotStore.search()`)이 유사 예제를 못 찾음.

### 5-2. 추가할 패턴 (최소 20개)

아래 5개 유형별로 4개씩, 총 20개 이상 추가. 실제 TC DB의 테이블명/컬럼명에 맞게 수정할 것.

**파일:** `config/few_shot/sql_seed.yaml`

```yaml
# 유형 1: 존재 확인 (단일 설비 + 단일 파라미터)
- question: "EQP001 설비에 PARAM_TEMP 파라미터 있어?"
  sql: |
    SELECT PARAM_NAME, PARAM_VALUE
    FROM TC_EQP_PARAM
    WHERE EQPID = 'EQP001' AND PARAM_NAME = 'PARAM_TEMP'
    LIMIT 10
  tags: [existence_check, single_eqp, single_param]

- question: "L01 라인 EQP002 설비에 AUTO_MODE 파라미터 존재하나?"
  sql: |
    SELECT PARAM_NAME
    FROM TC_EQP_PARAM
    WHERE EQPID = 'EQP002' AND PARAM_NAME = 'AUTO_MODE' AND LINEID = 'L01'
    LIMIT 1
  tags: [existence_check, with_lineid]

- question: "EQP003 설비 모델 정보 알려줘"
  sql: |
    SELECT EQPID, SERVER_MODEL
    FROM TC_EQUIPMENT
    WHERE EQPID = 'EQP003'
    LIMIT 10
  tags: [equipment_info]

- question: "EQP004 설비 SERVER_MODEL이 뭐야?"
  sql: |
    SELECT SERVER_MODEL
    FROM TC_EQUIPMENT
    WHERE EQPID = 'EQP004'
  tags: [equipment_info, single_column]

# 유형 2: 설비 비교
- question: "EQP001과 EQP002 설비의 파라미터 차이는?"
  sql: |
    SELECT a.PARAM_NAME, 'EQP001 전용' AS NOTE
    FROM TC_EQP_PARAM a
    WHERE a.EQPID = 'EQP001'
      AND a.PARAM_NAME NOT IN (
        SELECT PARAM_NAME FROM TC_EQP_PARAM WHERE EQPID = 'EQP002'
      )
    UNION ALL
    SELECT b.PARAM_NAME, 'EQP002 전용'
    FROM TC_EQP_PARAM b
    WHERE b.EQPID = 'EQP002'
      AND b.PARAM_NAME NOT IN (
        SELECT PARAM_NAME FROM TC_EQP_PARAM WHERE EQPID = 'EQP001'
      )
    LIMIT 100
  tags: [comparison, two_eqp]

- question: "EQP005에는 있고 EQP006에는 없는 파라미터가 뭐야?"
  sql: |
    SELECT PARAM_NAME
    FROM TC_EQP_PARAM
    WHERE EQPID = 'EQP005'
      AND PARAM_NAME NOT IN (
        SELECT PARAM_NAME FROM TC_EQP_PARAM WHERE EQPID = 'EQP006'
      )
    LIMIT 50
  tags: [comparison, not_in]

- question: "EQP001과 EQP002 두 설비 모두에 있는 파라미터는?"
  sql: |
    SELECT PARAM_NAME
    FROM TC_EQP_PARAM
    WHERE EQPID = 'EQP001'
      AND PARAM_NAME IN (
        SELECT PARAM_NAME FROM TC_EQP_PARAM WHERE EQPID = 'EQP002'
      )
    LIMIT 50
  tags: [comparison, intersection]

- question: "EQP003과 EQP004 설비 모델명 비교해줘"
  sql: |
    SELECT EQPID, SERVER_MODEL
    FROM TC_EQUIPMENT
    WHERE EQPID IN ('EQP003', 'EQP004')
  tags: [comparison, equipment_info]

# 유형 3: 집계/목록
- question: "L01 라인에 설비가 몇 개야?"
  sql: |
    SELECT COUNT(*) AS EQP_COUNT
    FROM TC_EQUIPMENT
    WHERE LINEID = 'L01'
  tags: [aggregation, count]

- question: "EQP001 설비 파라미터 몇 개야?"
  sql: |
    SELECT COUNT(*) AS PARAM_COUNT
    FROM TC_EQP_PARAM
    WHERE EQPID = 'EQP001'
  tags: [aggregation, count, single_eqp]

- question: "L01 라인 설비 목록 보여줘"
  sql: |
    SELECT EQPID, SERVER_MODEL
    FROM TC_EQUIPMENT
    WHERE LINEID = 'L01'
    ORDER BY EQPID
    LIMIT 100
  tags: [list, with_lineid]

- question: "파라미터가 가장 많은 설비 상위 5개"
  sql: |
    SELECT EQPID, COUNT(*) AS PARAM_COUNT
    FROM TC_EQP_PARAM
    GROUP BY EQPID
    ORDER BY PARAM_COUNT DESC
    LIMIT 5
  tags: [aggregation, ranking, group_by]

# 유형 4: 조건 필터
- question: "EQP001 설비에서 CEID가 CE001인 연결 정보 알려줘"
  sql: |
    SELECT EQP_ID, CEID, LINK_TYPE
    FROM TC_EQP_RELINK
    WHERE EQP_ID = 'EQP001' AND CEID = 'CE001'
    LIMIT 10
  tags: [filter, relink]

- question: "PARAM_TEMP 파라미터 가진 설비 목록"
  sql: |
    SELECT DISTINCT EQPID
    FROM TC_EQP_PARAM
    WHERE PARAM_NAME = 'PARAM_TEMP'
    LIMIT 100
  tags: [filter, by_param]

- question: "L02 라인 EQP007 설비 파라미터 전체 목록"
  sql: |
    SELECT PARAM_NAME, PARAM_VALUE
    FROM TC_EQP_PARAM
    WHERE EQPID = 'EQP007' AND LINEID = 'L02'
    ORDER BY PARAM_NAME
    LIMIT 200
  tags: [list, filter, with_lineid]

- question: "EQP008 설비의 연결된 CE 목록"
  sql: |
    SELECT CEID, LINK_TYPE
    FROM TC_EQP_RELINK
    WHERE EQP_ID = 'EQP008'
    LIMIT 50
  tags: [list, relink]

# 유형 5: 복합 조건
- question: "모든 라인에서 PARAM_TEMP 파라미터 없는 설비는?"
  sql: |
    SELECT EQPID, LINEID
    FROM TC_EQUIPMENT
    WHERE EQPID NOT IN (
      SELECT DISTINCT EQPID
      FROM TC_EQP_PARAM
      WHERE PARAM_NAME = 'PARAM_TEMP'
    )
    LIMIT 100
  tags: [complex, not_in, cross_table]

- question: "L01 라인에서 파라미터가 10개 이상인 설비"
  sql: |
    SELECT EQPID, COUNT(*) AS PARAM_COUNT
    FROM TC_EQP_PARAM
    WHERE LINEID = 'L01'
    GROUP BY EQPID
    HAVING COUNT(*) >= 10
    ORDER BY PARAM_COUNT DESC
    LIMIT 50
  tags: [complex, having, with_lineid]

- question: "EQP001과 EQP002 두 설비의 파라미터 수 비교"
  sql: |
    SELECT EQPID, COUNT(*) AS PARAM_COUNT
    FROM TC_EQP_PARAM
    WHERE EQPID IN ('EQP001', 'EQP002')
    GROUP BY EQPID
  tags: [complex, comparison, aggregation]

- question: "PARAM_FLOW 파라미터가 있는 설비 중 L01 라인인 것"
  sql: |
    SELECT e.EQPID, e.LINEID, e.SERVER_MODEL
    FROM TC_EQUIPMENT e
    INNER JOIN TC_EQP_PARAM p ON e.EQPID = p.EQPID
    WHERE p.PARAM_NAME = 'PARAM_FLOW'
      AND e.LINEID = 'L01'
    LIMIT 50
  tags: [complex, join, with_lineid]
```

**구현 시 주의사항:**
- 위 YAML의 테이블명/컬럼명(`TC_EQUIPMENT`, `EQPID`, `LINEID` 등)을 `config/whitelist.yaml`의 실제 값과 일치시킬 것.
- 실제 TC DB에 없는 컬럼이 있으면 수정.
- `FewShotStore.add_seed()`가 이 YAML을 읽어 TF-IDF 인덱스 구축 → SQL 생성 시 top-3 유사 예제를 LLM 프롬프트에 주입.

---

## 6. T5: tc_schema.yaml description 품질 개선

### 6-1. 배경

`SchemaStore`는 TF-IDF로 질문과 schema의 description을 매칭해 관련 테이블/컬럼을 선택. description이 짧거나 동의어가 없으면 잘못된 테이블이 선택됨.

### 6-2. description 작성 원칙

`config/schema/tc_schema.yaml`의 각 테이블/컬럼 description을 아래 원칙으로 보완:

```yaml
# 나쁜 예 (검색 안 됨)
- name: EQPID
  description: "설비 ID"

# 좋은 예 (동의어 + 형식 + 예시)
- name: EQPID
  description: "설비 고유 식별자. 장비ID, 설비명, 장비코드라고도 부름. 형식: 'EQP001'. WHERE EQPID = '설비명' 조건에 사용"
  sample_values: ["EQP001", "EQP002", "EQP_A_001"]
```

**각 컬럼 description에 포함할 것:**
1. 컬럼의 용도 (1줄)
2. 사용자가 질문에서 쓰는 표현 (동의어: "설비ID", "장비명", "장비코드" 등)
3. 값의 형식/패턴
4. 어떤 WHERE/JOIN 조건에 주로 쓰이는지

### 6-3. description 품질 검증 스크립트 — `scripts/check_schema_descriptions.py`

**이 파일을 신규 생성할 것.**

```python
#!/usr/bin/env python
# scripts/check_schema_descriptions.py
# tc_schema.yaml의 description 품질을 검사해서 부실한 항목 출력

import yaml
import sys
from pathlib import Path

SCHEMA_PATH = Path("config/schema/tc_schema.yaml")
MIN_DESC_LENGTH = 20  # description이 이 글자 수 미만이면 경고


def check():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    warnings = []
    tables = data.get("tables", {})

    for table_name, table_info in tables.items():
        t_desc = table_info.get("description", "")
        if len(t_desc) < MIN_DESC_LENGTH:
            warnings.append(f"[테이블] {table_name}: description 너무 짧음 ({len(t_desc)}자): '{t_desc}'")

        for col in table_info.get("columns", []):
            c_name = col.get("name", "")
            c_desc = col.get("description", "")
            if not c_desc:
                warnings.append(f"[컬럼] {table_name}.{c_name}: description 없음")
            elif len(c_desc) < MIN_DESC_LENGTH:
                warnings.append(f"[컬럼] {table_name}.{c_name}: description 너무 짧음 ({len(c_desc)}자): '{c_desc}'")

    if warnings:
        print(f"⚠️  description 품질 경고 {len(warnings)}건:\n")
        for w in warnings:
            print(f"  {w}")
        sys.exit(1)
    else:
        print(f"✅ description 품질 OK ({sum(len(t.get('columns',[])) for t in tables.values())}개 컬럼 확인)")


if __name__ == "__main__":
    check()
```

**실행:**
```bash
py scripts/check_schema_descriptions.py
```

---

## 7. T6: sql_gen.j2 Chain-of-Thought + MySQL 문법 강화

### 7-1. 배경

현재 `config/prompts/sql_gen.j2`는 Claude 기준으로 작성. GPT-OSS/Gemma4 계열 소형 LLM은:
- JSON 출력 불안정 (코드블록 감싸거나 설명 추가)
- MySQL LIMIT 대신 ROWNUM 쓰는 경우 있음
- 복잡한 쿼리에서 단계적 사고 없이 틀린 SQL 생성

### 7-2. `config/prompts/sql_gen.j2` 개선

**수정할 파일:** `config/prompts/sql_gen.j2`

아래 요소를 반드시 포함:

```jinja2
당신은 MySQL 8.0 전문가입니다. TC 설비 데이터베이스를 조회하는 SQL을 생성합니다.

## 데이터베이스 규칙 (반드시 준수)
- 행 제한: `LIMIT n` 사용 (ROWNUM 절대 사용 금지)
- 현재 시각: `NOW()` 또는 `CURRENT_TIMESTAMP`
- 문자열 연결: `CONCAT(a, b)` (|| 사용 금지)
- 파라미터 없는 SELECT: `SELECT 1` (FROM DUAL 사용 금지)
- 허용 테이블만 사용: {{ allowed_tables | join(', ') }}

## 사용 가능한 스키마
{{ schema_context }}

## Few-shot 예시
{% for ex in few_shot_examples %}
질문: {{ ex.question }}
SQL:
```sql
{{ ex.sql }}
```
---
{% endfor %}

## 단계별 사고 (Chain-of-Thought)
SQL 작성 전 반드시 아래 순서로 생각하세요:
1. 질문에서 핵심 엔티티 추출 (설비명, 파라미터명, 라인ID 등)
2. 어떤 테이블이 필요한가? (JOIN 필요 여부)
3. WHERE 조건은? (있어야 할 필터)
4. 집계/정렬/LIMIT 필요한가?
5. SQL 작성

## 질문
{{ question }}

## 응답 형식 (JSON만 출력, 다른 텍스트 금지)
```json
{
  "reasoning": "1. 엔티티: ... 2. 테이블: ... 3. WHERE: ... 4. 기타: ...",
  "sql": "SELECT ...",
  "confidence": 0.9,
  "tables_used": ["TC_EQUIPMENT"]
}
```
```

**주의:**
- `reasoning` 필드는 LLM이 단계별로 생각하게 강제. 실제로 confidence 향상 효과 있음.
- `confidence`는 LLM 자체 추정치. 최종 confidence는 코드에서 실행 결과와 조합.
- `allowed_tables` 변수는 `SQLGenerator`에서 whitelist 기반으로 주입.

### 7-3. SQLGenerator 수정

`app/core/agents/db/sql_generator.py`에서 프롬프트 렌더링 시 `allowed_tables` 추가:

```python
# SQLGenerator.generate() 내부
prompt = self.renderer.render(
    "sql_gen",
    question=question,
    schema_context=schema_context,
    few_shot_examples=few_shot_examples,
    allowed_tables=list(self.validator.allowed_tables),  # 추가
)
```

---

## 8. T7: Golden Eval 실행 + Baseline 고정

### 8-1. 사전 조건

T1~T6 완료 후 실행. 서버 기동 확인 후 진행.

### 8-2. 실행

```bash
# 1. 서버 기동 확인
curl http://localhost:8000/health

# 2. Golden Eval 실행 (실제 LLM 호출, ~30분 소요)
py -m pytest -m real_llm tests/golden/test_golden_regression.py -v -s

# 3. 결과 확인
# 출력 예시:
# [Golden Eval] run_id=1 score=0.731
# PASSED tests/golden/test_golden_regression.py::test_golden_phase1_no_regression
```

### 8-3. Baseline 고정

결과의 `overall_score`를 `tests/golden/datasets/db_phase1.yaml` 상단에 기록:

```yaml
# tests/golden/datasets/db_phase1.yaml
baseline_score: 0.73  # 실측값으로 교체. 이후 모든 PR은 이 값 -0.05 이상이어야 함
```

**주의:** baseline은 한 번 고정하면 낮추지 말 것. 개선 시에만 올릴 것.

### 8-4. 결과 확인 쿼리

```sql
-- 최근 실행 결과 확인
SELECT run_id, dataset, overall_score, passed, total,
       baseline, regression, git_sha, created_at
FROM eval_run
ORDER BY created_at DESC
LIMIT 10;

-- 실패한 케이스 분석
SELECT case_key, difficulty, score, failure_reasons, generated_sql
FROM eval_case
WHERE run_id = 1 AND passed = 0
ORDER BY score ASC;
```

---

## 9. 테스트 체크리스트

구현 완료 후 아래를 순서대로 확인:

```bash
# 1. 단위 테스트 (새로 추가한 EvalRepository 포함)
py -m pytest tests/unit -v

# 2. 통합 테스트
py -m pytest tests/integration -v

# 3. description 품질 검사
py scripts/check_schema_descriptions.py

# 4. 전체 (real_llm 제외)
py -m pytest -m "not real_llm" -v

# 5. Golden Eval (사내 환경, 실제 LLM)
py -m pytest -m real_llm tests/golden -v -s
```

**체크리스트:**
- [ ] `db/migrations/004_eval_tracking.sql` 실행 완료
- [ ] `py -m pytest tests/unit -v` 100% 통과
- [ ] `py -m pytest tests/integration -v` 100% 통과
- [ ] `py scripts/check_schema_descriptions.py` 경고 0건
- [ ] `py -m pytest -m real_llm tests/golden -v` 실행 완료
- [ ] `tests/golden/datasets/db_phase1.yaml`의 `baseline_score` 실측값으로 업데이트
- [ ] `eval_run` 테이블에 1건 이상 기록 확인

---

## 10. 파일 변경 요약

| 파일 | 변경 유형 | 태스크 |
|------|----------|--------|
| `db/migrations/004_eval_tracking.sql` | 신규 | T1 |
| `app/infra/db/eval_repo.py` | 신규 | T1 |
| `tests/unit/test_eval_repo.py` | 신규 | T1 |
| `tests/golden/runner.py` | 수정 | T1 |
| `app/api/deps.py` | 수정 (EvalRepository + ValueStore 로드) | T1, T3 |
| `tests/conftest.py` | 수정 (db_agent, eval_repo fixture 추가) | T2 |
| `tests/golden/test_golden_regression.py` | 수정 (eval_repo 연결) | T2 |
| `config/few_shot/sql_seed.yaml` | 수정 (20개 패턴 추가) | T4 |
| `config/schema/tc_schema.yaml` | 수정 (description 품질 개선) | T5 |
| `scripts/check_schema_descriptions.py` | 신규 | T5 |
| `config/prompts/sql_gen.j2` | 수정 (CoT + MySQL 강화) | T6 |
| `app/core/agents/db/sql_generator.py` | 수정 (allowed_tables 주입) | T6 |
| `tests/golden/datasets/db_phase1.yaml` | 수정 (baseline_score 고정) | T7 |

---

## 11. 논문 활용 방법

이 구현이 완료되면:

1. **실험 재현성:** `eval_run` 테이블에 `git_sha` 기록 → 어느 커밋에서 어떤 점수였는지 추적 가능
2. **Ablation Study:** T3(ValueStore) → T4(Few-shot) → T6(CoT) 순서로 각각 Golden Eval 실행 → 각 개선분이 점수에 미친 영향 측정
3. **결과 표 형태:**

| 구성 | EX Score | Valid SQL % | Hard EX |
|------|----------|-------------|---------|
| Baseline | ? | ? | ? |
| + ValueStore | ? | ? | ? |
| + Few-shot 20개 | ? | ? | ? |
| + Chain-of-Thought | ? | ? | ? |

4. **데이터 추출:**
```sql
SELECT r.git_sha, r.overall_score,
       SUM(CASE WHEN c.difficulty='hard' AND c.passed=1 THEN 1 ELSE 0 END) AS hard_passed,
       COUNT(CASE WHEN c.difficulty='hard' THEN 1 END) AS hard_total
FROM eval_run r
JOIN eval_case c ON r.run_id = c.run_id
WHERE r.dataset = 'db_phase1'
GROUP BY r.run_id
ORDER BY r.created_at;
```
