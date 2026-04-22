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
| T8 | Self-Consistency (다수결 SQL 선택) | 신규 | 2h |
| T9 | Execution-Guided Verification (결과 계약 검증) | 신규 | 2h |
| T10 | 시맨틱 평가 지표 (EX / Component Match / Valid SQL) | 신규 | 2h |
| T11 | Schema Linker 2단계 (TF-IDF → LLM 재랭킹) | 수정 | 2h |
| T12 | Query Log 기반 프롬프트 캐싱 | 신규 | 1.5h |
| T13 | Fuzzy Value Matching (rapidfuzz) | 신규 | 1h |
| T14 | Anti-pattern Few-shot YAML | 수정 | 1h |
| T15 | Active Learning Loop (저신뢰 케이스 큐) | 신규 | 2h |
| T16 | Multi-model Ensemble (GPT-OSS + Gemma4) | 신규 | 2h |
| T17 | SQL AST 정적 수정 (sqlglot) | 신규 | 1.5h |
| T18 | QueryPlanner Pre-filter + SmallTalkAgent (잡담/인사 라우팅) | 신규 | 1.5h |
| T19 | QueryPlanner Rule 신뢰도 스코어링 + 경량 LLM 분류기 | 수정 | 2h |
| T20 | Multi-Model Task Routing (ModelRouter + per-task 모델 할당) | 신규 | 2h |

### 이번에 구현하지 않는 것

- Splunk 이상 탐지 (별도 스펙)
- 신뢰도 캘리브레이션 (운영 데이터 쌓인 후)
- T8~T17은 T1~T7 완료 후 순서대로 구현 (기반 인프라가 먼저 필요)

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

## 9-2. T8~T17 고급 최적화 — 상세 구현 가이드

> **전제:** T1~T7 완료 후 진행. 각 태스크는 독립적으로 구현 가능하지만 T10은 T1 DB 인프라에 의존.

---

### T8. Self-Consistency (다수결 SQL 선택)

**목적:** 같은 질문에 대해 N개의 SQL을 생성하고 실행 결과가 동일한 SQL을 선택 → 비결정적 LLM의 오류를 평균화.

**배경:** LLM은 같은 입력에 다른 SQL을 생성할 수 있음. 3개 중 2개가 같은 결과를 반환하면 그 결과가 맞을 가능성이 높음.

**구현 위치:** `app/core/agents/db/agent.py` → `_generate_sql()` 메서드 확장

```python
# app/core/agents/db/agent.py

import asyncio
from collections import Counter
from typing import Optional

async def _generate_with_consistency(
    self,
    question: str,
    schema_context: str,
    n_samples: int = 3,
    temperature: float = 0.3,
) -> tuple[str, float]:
    """N개 SQL 생성 후 실행 결과 기준 다수결 선택.
    Returns: (best_sql, confidence)  confidence = 최다 득표수 / n_samples
    """
    candidates: list[tuple[str, int]] = []  # (sql, result_hash)

    async def _sample():
        sql = await self.sql_generator.generate(
            question, schema_context, temperature=temperature
        )
        try:
            rows = await self.tc_pool.fetch_all(sql, max_rows=200)
            # 결과 집합을 정렬 후 해시 (순서 무관 비교)
            result_hash = hash(tuple(sorted(str(r) for r in rows)))
            return sql, result_hash
        except Exception:
            return None

    results = await asyncio.gather(*[_sample() for _ in range(n_samples)])
    valid = [r for r in results if r is not None]

    if not valid:
        # 모두 실패 → 단일 샘플로 폴백
        return await self.sql_generator.generate(question, schema_context), 0.0

    # 결과 해시 기준 다수결
    hash_counts = Counter(result_hash for _, result_hash in valid)
    best_hash, best_count = hash_counts.most_common(1)[0]
    best_sql = next(sql for sql, h in valid if h == best_hash)
    confidence = best_count / n_samples
    return best_sql, confidence
```

**설정 추가:** `config/agents.yaml`
```yaml
db_agent:
  self_consistency:
    enabled: true
    n_samples: 3          # 운영 비용 고려: 3이 기본
    temperature: 0.3      # 약간의 다양성 허용
    min_confidence: 0.5   # confidence < 0.5면 검토 큐에 등록
```

**테스트:** `tests/unit/test_self_consistency.py`
```python
@pytest.mark.asyncio
async def test_majority_vote_picks_most_common_result():
    # 3개 중 2개가 같은 결과 → 2개짜리 선택
    ...
```

---

### T9. Execution-Guided Verification (결과 계약 검증)

**목적:** SQL 실행 결과가 "의미 있는 결과"인지 계약으로 검증. 빈 결과, 전체 스캔, 비정상 카디널리티를 탐지.

**구현 위치:** `app/core/agents/db/verification.py` (신규 파일)

```python
# app/core/agents/db/verification.py

from dataclasses import dataclass
from typing import Any

LARGE_TABLES = {"TC_EQP_PARAM", "TC_EQP_RELINK"}  # WHERE 필수 테이블

@dataclass
class VerificationResult:
    passed: bool
    checks: dict[str, bool]
    warnings: list[str]

def verify_execution_result(
    sql: str,
    rows: list[dict[str, Any]],
    question: str,
) -> VerificationResult:
    sql_upper = sql.upper()
    checks = {}
    warnings = []

    # 1. 결과 비어있지 않음
    checks["non_empty"] = len(rows) > 0
    if not checks["non_empty"]:
        warnings.append("결과가 비어 있음 — 필터 조건이 너무 좁거나 데이터 없음")

    # 2. 카디널리티 합리적 (1 ~ 10,000행)
    checks["cardinality_reasonable"] = 1 <= len(rows) <= 10_000
    if len(rows) > 10_000:
        warnings.append(f"결과가 {len(rows)}행 — LIMIT 또는 WHERE 조건 부족 가능성")

    # 3. 대형 테이블 전체 스캔 방지
    needs_where = any(t in sql_upper for t in LARGE_TABLES)
    checks["no_full_scan"] = not needs_where or "WHERE" in sql_upper
    if not checks["no_full_scan"]:
        warnings.append("대형 테이블 전체 스캔 감지 — WHERE 조건 없음")

    # 4. SELECT * 방지
    checks["no_select_star"] = "SELECT *" not in sql_upper
    if not checks["no_select_star"]:
        warnings.append("SELECT * 사용 — 명시적 컬럼 지정 권장")

    passed = all(checks.values())
    return VerificationResult(passed=passed, checks=checks, warnings=warnings)
```

**Agent 연동:** `app/core/agents/db/agent.py`의 실행 단계 이후:
```python
verification = verify_execution_result(sql, rows, question)
if not verification.passed:
    # 검증 실패 → refine_loop에 힌트 전달
    hint = "; ".join(verification.warnings)
    sql = await self._refine_sql(sql, hint)
```

**테스트:** `tests/unit/test_verification.py`
```python
def test_empty_result_fails():
    result = verify_execution_result("SELECT ...", [], "...")
    assert not result.passed
    assert not result.checks["non_empty"]

def test_large_table_no_where_fails():
    result = verify_execution_result(
        "SELECT EQPID FROM TC_EQP_PARAM", [{"EQPID": "A"}], "..."
    )
    assert not result.checks["no_full_scan"]
```

---

### T10. 시맨틱 평가 지표 (Execution Accuracy / Component Match / Valid SQL Rate)

**목적:** 현재 Golden Eval은 키워드 매칭만 사용. Spider/BIRD 벤치마크 수준의 정밀 지표 추가.

#### 10-1. Golden YAML에 `expected_sql` 필드 추가

```yaml
# tests/golden/datasets/db_phase1.yaml — 케이스 예시 (수정)
cases:
  - id: db_001
    question: "L01 라인에서 최근 교체된 설비는?"
    expected_keywords: ["LINEID", "L01"]
    expected_sql: >-
      SELECT EQPID, REPLACE_DATE
      FROM TC_EQUIPMENT
      WHERE LINEID = 'L01'
      ORDER BY REPLACE_DATE DESC
      LIMIT 10
    difficulty: medium
```

> `expected_sql`은 선택 필드. 없으면 기존 키워드 매칭만 사용. 있으면 EX / Component Match 추가 계산.

#### 10-2. `tests/golden/metrics.py` 확장

```python
# tests/golden/metrics.py (수정)

import sqlglot
from typing import Any

async def execution_accuracy(
    actual_sql: str,
    expected_sql: str,
    tc_pool,
    max_rows: int = 500,
) -> float:
    """실행 결과 집합 동치 비교. 0.0 또는 1.0."""
    try:
        actual_rows = await tc_pool.fetch_all(actual_sql, max_rows=max_rows)
        expected_rows = await tc_pool.fetch_all(expected_sql, max_rows=max_rows)
        actual_set = frozenset(tuple(sorted(r.items())) for r in actual_rows)
        expected_set = frozenset(tuple(sorted(r.items())) for r in expected_rows)
        return 1.0 if actual_set == expected_set else 0.0
    except Exception:
        return 0.0


def component_match(actual_sql: str, expected_sql: str) -> dict[str, bool]:
    """SELECT / FROM / WHERE 절 개별 일치 여부."""
    def _extract(sql: str, clause: str) -> set[str]:
        try:
            ast = sqlglot.parse_one(sql, dialect="mysql")
            node = ast.find(getattr(sqlglot.expressions, clause, None))
            return {str(c).upper() for c in (node.expressions if node else [])}
        except Exception:
            return set()

    return {
        "select_match": _extract(actual_sql, "Select") == _extract(expected_sql, "Select"),
        "from_match": _extract(actual_sql, "From") == _extract(expected_sql, "From"),
        "where_match": _extract(actual_sql, "Where") == _extract(expected_sql, "Where"),
    }


def valid_sql_rate(sql_list: list[str]) -> float:
    """파싱 가능한 SQL 비율 (문법 오류 없음)."""
    valid = 0
    for sql in sql_list:
        try:
            sqlglot.parse_one(sql, dialect="mysql")
            valid += 1
        except Exception:
            pass
    return valid / len(sql_list) if sql_list else 0.0
```

#### 10-3. `eval_case` 테이블 컬럼 추가 (DDL 수정)

```sql
-- db/migrations/005_eval_metrics.sql
ALTER TABLE eval_case
  ADD COLUMN execution_accuracy DECIMAL(5,4) AFTER score,
  ADD COLUMN select_match       TINYINT(1)   AFTER execution_accuracy,
  ADD COLUMN from_match         TINYINT(1)   AFTER select_match,
  ADD COLUMN where_match        TINYINT(1)   AFTER from_match;
```

**의존성 추가:** `requirements.txt`
```
sqlglot>=23.0.0
```

---

### T11. Schema Linker 2단계 (TF-IDF → LLM 재랭킹)

**목적:** 현재 Schema Linker는 TF-IDF만 사용. 의미적으로 유사하지만 키워드가 다른 컬럼을 놓침. LLM이 후보 10개 중 실제 필요한 것을 선택하게 함.

**구현 위치:** `app/core/agents/db/schema_linker.py` (수정)

```python
# app/core/agents/db/schema_linker.py (수정)

class SchemaLinker:
    def __init__(self, schema_store, llm_provider, whitelist):
        self.schema_store = schema_store
        self.llm = llm_provider
        self.whitelist = whitelist

    async def link(self, question: str) -> list[str]:
        # Stage 1: TF-IDF로 후보 10개 추출
        candidates = self.schema_store.search(question, top_k=10)

        # Stage 2: LLM이 실제 필요한 테이블/컬럼 선택
        prompt = self._build_rerank_prompt(question, candidates)
        selected = await self.llm.complete_json(prompt)
        return selected.get("relevant_columns", candidates[:5])

    def _build_rerank_prompt(self, question: str, candidates: list[str]) -> str:
        return (
            f"질문: {question}\n\n"
            f"후보 테이블/컬럼:\n" + "\n".join(f"- {c}" for c in candidates) +
            "\n\n위 후보 중 이 질문에 필요한 것만 JSON 배열로 반환하세요."
            '\n{"relevant_columns": ["TABLE.COLUMN", ...]}'
        )
```

**프롬프트 분리:** `config/prompts/schema_rerank.j2`
```jinja2
질문: {{ question }}

후보 스키마:
{% for col in candidates %}
- {{ col }}
{% endfor %}

이 질문을 SQL로 변환하기 위해 실제 필요한 테이블/컬럼만 골라서 JSON으로 반환하세요.
{"relevant_columns": ["테이블명.컬럼명", ...]}
```

**테스트:** `tests/unit/test_schema_linker_rerank.py`
```python
@pytest.mark.asyncio
async def test_rerank_removes_irrelevant_candidates():
    mock_llm = AsyncMock()
    mock_llm.complete_json.return_value = {
        "relevant_columns": ["TC_EQUIPMENT.EQPID", "TC_EQUIPMENT.LINEID"]
    }
    linker = SchemaLinker(schema_store=..., llm_provider=mock_llm, whitelist=...)
    result = await linker.link("L01 라인 설비 목록")
    assert "TC_EQUIPMENT.EQPID" in result
    assert len(result) <= 5  # 불필요한 컬럼 제거됨
```

---

### T12. Query Log 기반 프롬프트 캐싱

**목적:** 동일한 질문(또는 매우 유사한 질문)에 대해 이전에 성공한 SQL을 재사용. LLM 호출 비용 절감 + 응답 속도 향상.

**구현 위치:** `app/infra/db/query_log.py` (기존 파일 수정) + `app/core/agents/db/agent.py`

```python
# app/infra/db/query_log.py (수정)

import hashlib
from datetime import datetime, timedelta

class QueryLogRepository:
    # ... 기존 코드 ...

    async def get_cached_sql(
        self,
        question: str,
        ttl_hours: int = 24,
    ) -> str | None:
        """question_hash로 최근 성공 SQL 조회. TTL 내에만 반환."""
        question_hash = hashlib.sha256(question.encode()).hexdigest()
        row = await self.pool.fetch_one(
            """
            SELECT sql_text FROM query_log
            WHERE question_hash = %s
              AND success = 1
              AND created_at > %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            question_hash,
            datetime.now() - timedelta(hours=ttl_hours),
        )
        return row["sql_text"] if row else None

    async def save_success(self, question: str, sql: str) -> None:
        question_hash = hashlib.sha256(question.encode()).hexdigest()
        await self.pool.execute(
            """
            INSERT INTO query_log (question_hash, question_text, sql_text, success)
            VALUES (%s, %s, %s, 1)
            ON DUPLICATE KEY UPDATE sql_text = %s, created_at = CURRENT_TIMESTAMP
            """,
            question_hash, question, sql, sql,
        )
```

**DDL 추가:** `db/migrations/006_query_log_cache.sql`
```sql
ALTER TABLE query_log
  ADD COLUMN question_hash CHAR(64) AFTER question_text,
  ADD INDEX idx_query_log_hash (question_hash, success, created_at);
```

**Agent 연동:**
```python
# app/core/agents/db/agent.py — run() 메서드 시작 부분

cached_sql = await self.query_log.get_cached_sql(question, ttl_hours=24)
if cached_sql:
    rows = await self.tc_pool.fetch_all(cached_sql, max_rows=500)
    return self._format_result(rows, from_cache=True)
```

---

### T13. Fuzzy Value Matching (rapidfuzz)

**목적:** 사용자가 설비명을 오타/축약으로 입력했을 때 가장 유사한 실제 값으로 매칭. ValueStore와 연동.

**의존성:** `requirements.txt`에 `rapidfuzz>=3.0.0` 추가

**구현 위치:** `app/core/agents/db/value_matcher.py` (신규 파일)

```python
# app/core/agents/db/value_matcher.py

from rapidfuzz import process, fuzz
from typing import Any

class ValueMatcher:
    """ValueStore의 실제 값과 사용자 입력을 Fuzzy 매칭."""

    def __init__(self, value_store, threshold: int = 80):
        self.value_store = value_store
        self.threshold = threshold

    def find_best(self, user_input: str, column: str) -> str | None:
        """column 값 목록에서 user_input과 가장 유사한 값 반환.
        threshold 미만이면 None 반환 (매칭 실패).
        """
        known_values = self.value_store.get_values(column)
        if not known_values:
            return None

        match, score, _ = process.extractOne(
            user_input,
            known_values,
            scorer=fuzz.WRatio,  # 부분 문자열 + 전치 오류에 강함
        )
        return match if score >= self.threshold else None

    def enrich_filters(self, filters: dict[str, str]) -> dict[str, str]:
        """필터 딕셔너리의 모든 값에 Fuzzy 매칭 적용.
        매칭 성공 시 교체, 실패 시 원본 유지.
        """
        enriched = {}
        for col, val in filters.items():
            best = self.find_best(val, col)
            enriched[col] = best if best else val
        return enriched
```

**Agent 연동:** `app/core/agents/db/agent.py`
```python
# run() 메서드에서 schema_link 이후
enriched_filters = self.value_matcher.enrich_filters(extracted_filters)
```

**테스트:** `tests/unit/test_value_matcher.py`
```python
def test_fuzzy_match_typo():
    store = MagicMock()
    store.get_values.return_value = ["EQPA-001", "EQPB-002", "EQPC-003"]
    matcher = ValueMatcher(store, threshold=80)
    assert matcher.find_best("EQPA001", "EQPID") == "EQPA-001"  # 하이픈 누락

def test_fuzzy_match_below_threshold_returns_none():
    store = MagicMock()
    store.get_values.return_value = ["EQPA-001", "EQPB-002"]
    matcher = ValueMatcher(store, threshold=80)
    assert matcher.find_best("XXXXXXXXX", "EQPID") is None
```

---

### T14. Anti-pattern Few-shot YAML

**목적:** LLM에게 "이렇게 하면 안 된다"는 예시를 제공. 특히 전체 스캔, SELECT * 등의 나쁜 패턴을 피하도록 유도.

**파일 위치:** `config/few_shot/sql_antipatterns.yaml` (신규 파일)

```yaml
# config/few_shot/sql_antipatterns.yaml
# LLM 프롬프트에 주입되는 Anti-pattern 예시
# 형식: bad_question + bad_sql + correction + good_sql

antipatterns:
  - id: ap_001
    bad_question: "TC_EQP_PARAM 테이블의 모든 데이터 보여줘"
    bad_sql: "SELECT * FROM TC_EQP_PARAM"
    correction: |
      TC_EQP_PARAM은 대형 테이블 (수백만 행). WHERE 조건과 LIMIT 없이 조회하면
      DB 과부하. 화이트리스트에서 필요한 컬럼만 명시적으로 선택해야 함.
    good_sql: |
      SELECT EQPID, PARAM_NAME, PARAM_VALUE
      FROM TC_EQP_PARAM
      WHERE LINEID = 'L01' AND EQPID = 'EQP-A001'
      LIMIT 100

  - id: ap_002
    bad_question: "모든 설비 목록"
    bad_sql: "SELECT * FROM TC_EQUIPMENT"
    correction: |
      WHERE 없는 전체 조회는 운영 DB에 부하. 라인/타입 등 필터 유도 또는
      LIMIT 명시. SELECT *는 불필요한 컬럼 포함.
    good_sql: |
      SELECT EQPID, SERVER_MODEL, LINEID
      FROM TC_EQUIPMENT
      WHERE LINEID = 'L01'
      ORDER BY EQPID
      LIMIT 50

  - id: ap_003
    bad_question: "EQPID가 EQP로 시작하는 거 다 줘"
    bad_sql: "SELECT * FROM TC_EQUIPMENT WHERE EQPID LIKE 'EQP%'"
    correction: |
      LIKE 'prefix%'는 인덱스 사용 가능하나 결과가 많을 수 있음.
      LIMIT 추가 필수. SELECT * 대신 필요 컬럼만.
    good_sql: |
      SELECT EQPID, SERVER_MODEL
      FROM TC_EQUIPMENT
      WHERE EQPID LIKE 'EQP%'
      ORDER BY EQPID
      LIMIT 100

  - id: ap_004
    bad_question: "TC_EQP_RELINK에서 CEID 뭐뭐 있어?"
    bad_sql: "SELECT DISTINCT CEID FROM TC_EQP_RELINK"
    correction: |
      TC_EQP_RELINK는 대형 테이블. DISTINCT + 전체 스캔은 느림.
      특정 EQP_ID 범위로 한정해야 함.
    good_sql: |
      SELECT DISTINCT CEID
      FROM TC_EQP_RELINK
      WHERE EQP_ID = 'EQP-A001'
      LIMIT 50
```

**프롬프트 연동:** `config/prompts/sql_gen.j2`에 anti-pattern 섹션 추가
```jinja2
{# sql_gen.j2 — anti-pattern 섹션 추가 #}
{% if antipatterns %}
## 절대 피해야 할 패턴

{% for ap in antipatterns %}
❌ 나쁜 예 ({{ ap.id }}):
```sql
{{ ap.bad_sql }}
```
이유: {{ ap.correction }}

✅ 올바른 예:
```sql
{{ ap.good_sql }}
```
{% endfor %}
{% endif %}
```

---

### T15. Active Learning Loop (저신뢰 케이스 큐)

**목적:** 신뢰도가 낮은 응답을 자동으로 검토 큐에 등록. 검토자 피드백을 Few-shot에 추가하여 모델 품질 향상.

**구현 흐름:**
```
Agent 실행 → confidence 계산 → [0.6, 0.8) 구간 → active_learning_queue 테이블 INSERT
→ 검토자 UI에서 승인/수정 → 승인된 케이스 → few_shot_example 테이블 INSERT
```

**DDL:** `db/migrations/007_active_learning.sql`
```sql
CREATE TABLE active_learning_queue (
  queue_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id    VARCHAR(36)   NOT NULL,
  question      TEXT          NOT NULL,
  generated_sql TEXT,
  confidence    DECIMAL(5,4)  NOT NULL,
  status        ENUM('pending', 'approved', 'rejected', 'modified') DEFAULT 'pending',
  reviewer_sql  TEXT,                          -- 검토자가 수정한 SQL
  reviewed_by   VARCHAR(100),
  reviewed_at   TIMESTAMP,
  created_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_alq_status (status, confidence),
  INDEX idx_alq_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**구현 위치:** `app/infra/db/active_learning_repo.py` (신규)
```python
# app/infra/db/active_learning_repo.py

CONFIDENCE_LOW = 0.6
CONFIDENCE_HIGH = 0.8

class ActiveLearningRepository:
    def __init__(self, pool):
        self.pool = pool

    async def maybe_enqueue(
        self, session_id: str, question: str, sql: str, confidence: float
    ) -> bool:
        """신뢰도가 [LOW, HIGH) 구간이면 큐에 등록. 등록 여부 반환."""
        if not (CONFIDENCE_LOW <= confidence < CONFIDENCE_HIGH):
            return False
        await self.pool.execute(
            """
            INSERT INTO active_learning_queue
              (session_id, question, generated_sql, confidence)
            VALUES (%s, %s, %s, %s)
            """,
            session_id, question, sql, confidence,
        )
        return True

    async def approve_and_promote(self, queue_id: int, reviewer: str) -> None:
        """승인 → few_shot_example 테이블에 자동 추가."""
        row = await self.pool.fetch_one(
            "SELECT * FROM active_learning_queue WHERE queue_id = %s", queue_id
        )
        final_sql = row["reviewer_sql"] or row["generated_sql"]

        await self.pool.execute(
            """
            INSERT INTO few_shot_example (question, sql_text, source)
            VALUES (%s, %s, 'active_learning')
            """,
            row["question"], final_sql,
        )
        await self.pool.execute(
            """
            UPDATE active_learning_queue
            SET status='approved', reviewed_by=%s, reviewed_at=CURRENT_TIMESTAMP
            WHERE queue_id=%s
            """,
            reviewer, queue_id,
        )
```

---

### T16. Multi-model Ensemble (GPT-OSS + Gemma4)

**목적:** 두 모델을 동시에 호출하여 결과를 비교. 불일치 시 검토 큐 등록. 일치 시 신뢰도 향상.

**구현 위치:** `app/infra/llm/ensemble.py` (신규)
```python
# app/infra/llm/ensemble.py

import asyncio
from app.infra.llm.base import LLMProvider

class EnsembleLLM:
    """두 LLM 동시 호출 → 결과 비교."""

    def __init__(self, primary: LLMProvider, secondary: LLMProvider):
        self.primary = primary
        self.secondary = secondary

    async def complete_sql(self, prompt: str) -> tuple[str, float]:
        """Returns: (best_sql, agreement_score)"""
        primary_sql, secondary_sql = await asyncio.gather(
            self.primary.complete(prompt),
            self.secondary.complete(prompt),
        )

        # 실행 결과 비교로 일치 여부 판단 (T8 hash 비교 활용)
        agreement = await self._execution_agreement(primary_sql, secondary_sql)

        if agreement >= 0.9:
            return primary_sql, agreement  # 높은 신뢰도
        else:
            # 불일치 → primary 반환하되 낮은 신뢰도
            return primary_sql, agreement

    async def _execution_agreement(self, sql1: str, sql2: str) -> float:
        """두 SQL 실행 결과 유사도 0.0~1.0."""
        try:
            rows1 = await self.tc_pool.fetch_all(sql1, max_rows=100)
            rows2 = await self.tc_pool.fetch_all(sql2, max_rows=100)
            set1 = frozenset(tuple(sorted(r.items())) for r in rows1)
            set2 = frozenset(tuple(sorted(r.items())) for r in rows2)
            if not set1 and not set2:
                return 1.0
            return len(set1 & set2) / len(set1 | set2) if set1 | set2 else 0.0
        except Exception:
            return 0.0
```

**설정:** `config/agents.yaml`
```yaml
db_agent:
  ensemble:
    enabled: false          # 비용 2배 → 기본 비활성화
    primary_model: gpt-oss
    secondary_model: gemma4
    min_agreement: 0.8      # 이 이하면 active_learning_queue 등록
```

---

### T17. SQL AST 정적 수정 (sqlglot)

**목적:** LLM이 생성한 SQL에 문법 오류나 방언(Oracle/MSSQL 문법 혼용)이 있을 때 LLM 재호출 없이 정적으로 수정.

**구현 위치:** `app/core/agents/db/ast_repair.py` (신규)
```python
# app/core/agents/db/ast_repair.py

import sqlglot
from sqlglot import expressions as exp

class SQLASTRepair:
    """sqlglot로 SQL 정적 분석 + 자동 수정."""

    TARGET_DIALECT = "mysql"

    def repair(self, sql: str) -> tuple[str, list[str]]:
        """Returns: (repaired_sql, applied_fixes)"""
        fixes = []
        try:
            ast = sqlglot.parse_one(sql, error_level=sqlglot.ErrorLevel.WARN)
        except Exception as e:
            # 파싱 불가 → 원본 반환
            return sql, [f"파싱 실패: {e}"]

        # Fix 1: Oracle ROWNUM → MySQL LIMIT
        if "ROWNUM" in sql.upper():
            sql = self._replace_rownum(sql)
            fixes.append("ROWNUM → LIMIT 변환")

        # Fix 2: Oracle NVL → MySQL IFNULL
        if "NVL(" in sql.upper():
            sql = sql.replace("NVL(", "IFNULL(").replace("nvl(", "IFNULL(")
            fixes.append("NVL → IFNULL 변환")

        # Fix 3: 방언 통일 (Oracle/MSSQL → MySQL)
        try:
            repaired = sqlglot.transpile(sql, read="oracle", write="mysql")[0]
            if repaired != sql:
                fixes.append("Oracle → MySQL 방언 변환")
                sql = repaired
        except Exception:
            pass

        # Fix 4: LIMIT 없는 대형 테이블 쿼리에 LIMIT 추가
        sql, limit_added = self._ensure_limit(sql)
        if limit_added:
            fixes.append("LIMIT 자동 주입")

        return sql, fixes

    def _replace_rownum(self, sql: str) -> str:
        # ROWNUM <= N → LIMIT N
        import re
        return re.sub(
            r"WHERE\s+ROWNUM\s*<=\s*(\d+)",
            r"LIMIT \1",
            sql,
            flags=re.IGNORECASE,
        )

    def _ensure_limit(self, sql: str, default_limit: int = 1000) -> tuple[str, bool]:
        if "LIMIT" in sql.upper():
            return sql, False
        return sql.rstrip(";") + f" LIMIT {default_limit}", True
```

**Agent 연동:** `app/core/agents/db/agent.py`
```python
# SQL 생성 직후, 실행 전
repaired_sql, fixes = self.ast_repair.repair(generated_sql)
if fixes:
    logger.info(f"AST 수정 적용: {fixes}")
generated_sql = repaired_sql
```

**테스트:** `tests/unit/test_ast_repair.py`
```python
def test_rownum_to_limit():
    repair = SQLASTRepair()
    sql, fixes = repair.repair("SELECT * FROM TC_EQUIPMENT WHERE ROWNUM <= 10")
    assert "LIMIT 10" in sql
    assert "ROWNUM → LIMIT 변환" in fixes

def test_nvl_to_ifnull():
    repair = SQLASTRepair()
    sql, fixes = repair.repair("SELECT NVL(EQPID, 'N/A') FROM TC_EQUIPMENT")
    assert "IFNULL" in sql
    assert "NVL → IFNULL 변환" in fixes

def test_limit_auto_injection():
    repair = SQLASTRepair()
    sql, fixes = repair.repair("SELECT EQPID FROM TC_EQUIPMENT WHERE LINEID='L01'")
    assert "LIMIT" in sql
    assert "LIMIT 자동 주입" in fixes
```

---

### T18. QueryPlanner Pre-filter + SmallTalkAgent (잡담/인사 라우팅)

**목적:** 현재 QueryPlanner의 default="db" 구조로 인해 "안녕", "고마워" 같은 잡담도 Text-to-SQL 파이프라인(20초)으로 진입하는 버그 해결. Pre-filter로 즉시 분기하여 100ms 내 응답.

**현재 문제:**
- `_classify_rule()` 어떤 패턴도 안 맞으면 무조건 `"db"` 반환 (line 33 in planner.py)
- "안녕" → db agent → 20초 소요 후 의미없는 SQL 시도
- 빈 메시지, 2글자 입력도 동일

**구현: `app/core/orchestrator/planner.py` 수정**

```python
# app/core/orchestrator/planner.py

import re
import uuid
from app.shared.schemas import SubQuery
from app.shared.logging import get_logger

logger = get_logger(__name__)

# --- 기존 패턴 유지 ---
_DOC_PATTERNS = [...]
_LOG_PATTERNS = [...]
_KNOWLEDGE_PATTERNS = [...]

# --- 신규: Pre-filter 패턴 ---
_SMALLTALK_PATTERNS = [
    r"^(안녕|하이|하이요|hello|hi|헬로)[\s.!?~ㅎㅋ]*$",
    r"^(고마워|감사|ㄱㅅ|thank|thx)",
    r"^(넵|네|응|ok|okay|알겠|오케이)[\s.!?~]*$",
    r"^[ㅋㅎㅠ\s]+$",          # 이모티콘만
]
_REJECT_PATTERNS = [
    r"^\s*$",                   # 공백만
    r"^.{0,1}$",                # 1글자 이하
]


def prefilter(msg: str) -> str | None:
    """즉각 분기 가능한 유형 식별.
    Returns: 'smalltalk' | 'reject' | None (None이면 계속 분류)
    """
    stripped = msg.strip()
    for pat in _REJECT_PATTERNS:
        if re.match(pat, stripped):
            return "reject"
    for pat in _SMALLTALK_PATTERNS:
        if re.match(pat, stripped, re.IGNORECASE):
            return "smalltalk"
    return None


class QueryPlanner:
    async def plan_async(self, message: str, session_id: str, ...) -> list[SubQuery]:
        # Stage 1: Pre-filter (0초)
        pre = prefilter(message)
        if pre in ("smalltalk", "reject"):
            return [SubQuery(id=str(uuid.uuid4()), agent=pre, query=message)]

        # Stage 2: Rule-based (0초)
        if self._llm and self._renderer:
            ...  # 기존 LLM 분류
        agent = _classify_rule(message)
        return [SubQuery(id=str(uuid.uuid4()), agent=agent, query=message)]
```

**신규 파일: `app/core/agents/smalltalk/agent.py`**

```python
# app/core/agents/smalltalk/agent.py

from app.core.agents.base import Agent, AgentResult
from app.shared.schemas import SubQuery
import re

class SmallTalkAgent(Agent):
    """잡담/인사/감사 전용 Agent. LLM 없이 템플릿으로 즉답."""

    _TEMPLATES = {
        "greeting": [
            r"안녕|hello|hi|하이",
            "안녕하세요! TC 설비 관련 질문을 해주시면 도와드리겠습니다.",
        ],
        "thanks": [
            r"고마워|감사|thank",
            "도움이 되셨다니 다행입니다. 추가 질문 있으시면 말씀해주세요.",
        ],
        "ack": [
            r"넵|네|응|ok|알겠",
            "네, 언제든지 질문해주세요.",
        ],
    }

    async def run(self, subquery: SubQuery, **kwargs) -> AgentResult:
        msg = subquery.query.strip().lower()
        for _, (pattern, response) in self._TEMPLATES.items():
            if re.search(pattern, msg, re.IGNORECASE):
                return AgentResult(
                    answer=response,
                    confidence=1.0,
                    agent="smalltalk",
                    latency_ms=1,
                )
        # 템플릿 미매치 → 범용 응답
        return AgentResult(
            answer="TC VOC 챗봇입니다. 설비 조회, 파라미터 확인, 장애 분석 등 질문해주세요.",
            confidence=1.0,
            agent="smalltalk",
            latency_ms=1,
        )
```

**`reject` 처리:** Orchestrator에서 `agent=="reject"` 감지 시 "질문을 입력해주세요." 즉답 반환 (Agent 불필요).

**테스트:** `tests/unit/test_planner_prefilter.py`
```python
def test_greeting_routes_to_smalltalk():
    pre = prefilter("안녕")
    assert pre == "smalltalk"

def test_empty_routes_to_reject():
    pre = prefilter("  ")
    assert pre == "reject"

def test_db_question_passes_through():
    pre = prefilter("L01 라인 설비 목록 알려줘")
    assert pre is None  # 계속 분류
```

---

### T19. QueryPlanner Rule 신뢰도 스코어링 + 경량 LLM 분류기

**목적:** 현재 `_classify_rule()` 은 매칭 여부만 판단하고 신뢰도를 반환하지 않음. default="db"로 낙찰되는 애매한 케이스를 경량 LLM에 위임하여 정확도 향상. 대형 LLM은 쓰지 않음.

**기존 문제:**
- "L01 설비가 이상해" → "이상" 패턴 없음 → db (정답: log)
- "PARAM_A 어떻게 써?" → "설명" 없음 → db (정답: doc)
- 신뢰도 정보 없어서 LLM 위임 여부를 판단할 수 없음

**구현: `_classify_rule()` 확장**

```python
def _classify_rule_with_confidence(msg: str) -> tuple[str, float]:
    """Returns: (agent, confidence 0.0~1.0)"""
    match_counts = {"log": 0, "doc": 0, "knowledge": 0}
    for pat in _LOG_PATTERNS:
        if re.search(pat, msg):
            match_counts["log"] += 1
    for pat in _DOC_PATTERNS:
        if re.search(pat, msg):
            match_counts["doc"] += 1
    for pat in _KNOWLEDGE_PATTERNS:
        if re.search(pat, msg):
            match_counts["knowledge"] += 1

    best = max(match_counts, key=match_counts.get)
    count = match_counts[best]

    if count >= 2:
        return best, 0.95   # 강한 매치
    if count == 1:
        return best, 0.65   # 약한 매치 → LLM 검토 권장
    return "db", 0.40       # 기본값, 낮은 신뢰도 → LLM 검토 권장
```

**경량 LLM 분류기 (신뢰도 < 0.7 인 경우만 호출)**

```python
# config/prompts/planner.j2 (신규)
당신은 질문을 아래 4가지 유형으로 분류하는 분류기입니다.

유형:
- db: 설비 데이터 조회 (SQL 필요)
- doc: 기능 설명, 동작 원리 설명 (문서 검색)
- log: 오동작, 에러, 장애 원인 분석 (로그 분석)
- knowledge: 운영 노하우, FAQ, 팁

질문: {{ question }}

JSON만 반환: {"agent": "<유형>", "reason": "<한 줄 근거>"}
```

```python
# QueryPlanner.plan_async()
agent, conf = _classify_rule_with_confidence(message)

if conf < 0.7 and self._llm_light and self._renderer:
    try:
        prompt = self._renderer.render("planner", question=message)
        result = await self._llm_light.complete_json(prompt)  # 경량 모델, ~1~2초
        agent = result.get("agent", agent)
        logger.info("planner_llm_classify", agent=agent, conf=conf)
    except Exception:
        pass  # 실패 시 rule 결과 사용

return [SubQuery(id=str(uuid.uuid4()), agent=agent, query=message)]
```

**설정:** `config/llm_routing.yaml`
```yaml
routing:
  planner: gemma4-4b        # 경량 모델, ~1~2초
  schema_linking: gemma4-4b
  sql_generation: gpt-oss
  refine: gpt-oss
  interpretation: gemma4-4b  # 또는 템플릿
```

**기대 효과:**

| 케이스 | 기존 | 개선 후 |
|--------|------|---------|
| "안녕" | db (20초) | smalltalk (<100ms) |
| "L01 설비가 이상해" | db (오분류) | log (경량 LLM 정분류, +1~2초) |
| "EQPID가 뭐야?" | doc (패턴 매치) | doc (동일, 0초) |
| "L01 라인 설비 목록" | db (고신뢰 rule) | db (0초) |

---

### T20. Multi-Model Task Routing (ModelRouter)

**목적:** 파이프라인 각 단계에 최적 모델을 할당. 경량 모델로 교체 가능한 단계를 분리하여 전체 레이턴시 단축. 사내에 여러 GPT 모델이 존재하는 환경에 맞게 config 기반으로 교체 가능하게 구성.

**레이턴시 목표:**

| 구성 | 예상 P50 |
|------|---------|
| 현재 (모두 대형 모델, 4콜 순차) | ~20초 |
| 경량 모델 Schema Linking + Interpretation | ~11초 |
| + Interpretation 템플릿화 (LLM 콜 제거) | ~8초 |
| + Query Log 캐시 히트 (T12) | <500ms |

**단계별 모델 권장 배정:**

| 단계 | 작업 성격 | 권장 모델 | 근거 |
|------|----------|-----------|------|
| Planner 분류 | 4-class 분류 | 경량 | 추론 깊이 불필요 |
| Schema Linking | 후보 중 선택 | 경량 | 분류 태스크 |
| SQL Generation | 복잡한 구조 추론 | 대형 | 가장 틀리기 쉬운 단계 |
| Refine (조건부) | 오류 디버깅 | 대형 | 복잡한 수정 |
| Interpretation | 자연어 생성 | 경량 or 템플릿 | 구조화 결과 → 템플릿 우선 |

**신규 파일: `app/infra/llm/router.py`**

```python
# app/infra/llm/router.py

from app.infra.llm.base import LLMProvider

class ModelRouter:
    """task 이름 → LLMProvider 매핑. config로 모델 교체 가능."""

    def __init__(self, providers: dict[str, LLMProvider], routing: dict[str, str]):
        # providers: {"gpt-oss": GptOssProvider(...), "gemma4-4b": GemmaProvider(...)}
        # routing:   {"sql_generation": "gpt-oss", "schema_linking": "gemma4-4b", ...}
        self._providers = providers
        self._routing = routing

    def get(self, task: str) -> LLMProvider:
        model_name = self._routing.get(task, "gpt-oss")  # 없으면 대형 모델 폴백
        provider = self._providers.get(model_name)
        if provider is None:
            raise ValueError(f"Unknown model '{model_name}' for task '{task}'")
        return provider
```

**설정 파일: `config/llm_routing.yaml`**

```yaml
# config/llm_routing.yaml
# task별 사용 모델 매핑. 모델명은 .env의 LLM_MODEL_* 변수와 일치.
routing:
  planner:          gemma4-4b   # 경량, ~1초
  schema_linking:   gemma4-4b   # 경량, ~2초
  sql_generation:   gpt-oss     # 대형, ~7초 (핵심 단계)
  refine:           gpt-oss     # 대형, 조건부 실행
  interpretation:   template    # 템플릿 우선, 복잡한 경우만 gemma4-4b

ensemble:
  sql_generation:
    enabled: false            # 비용 2배 → 기본 비활성화
    models: [gpt-oss, gpt-oss-v2]
    strategy: execution_majority
    timeout_sec: 12
    min_agreement: 0.8        # 이 이하면 active_learning_queue 등록
```

**`.env.example`에 추가:**
```bash
# Multi-model (ModelRouter용)
LLM_MODEL_LIGHT=gemma4-4b      # 경량 모델 (Planner, Schema Linking, Interpretation)
LLM_MODEL_HEAVY=gpt-oss        # 대형 모델 (SQL Generation, Refine)
LLM_API_BASE_URL_LIGHT=http://internal-llm-api/v1   # 경량 모델 엔드포인트 (대형과 다를 수 있음)
```

**Agent에서 사용:**
```python
# app/core/agents/db/agent.py
class DBAgent(Agent):
    def __init__(self, model_router: ModelRouter, ...):
        self.router = model_router

    async def run(self, subquery, **kwargs):
        schema_llm = self.router.get("schema_linking")   # 경량
        sql_llm = self.router.get("sql_generation")       # 대형
        interp_llm = self.router.get("interpretation")    # 경량 or template

        linked = await self.schema_linker.link(subquery.query, llm=schema_llm)
        sql = await self.sql_generator.generate(..., llm=sql_llm)
        answer = await self._interpret(sql, rows, llm=interp_llm)
```

**인터프리테이션 템플릿화 (LLM 콜 -1):**
```python
# config 라우팅이 "template"이면 LLM 없이 처리
def _interpret(self, sql, rows, llm) -> str:
    if self.router._routing.get("interpretation") == "template":
        return self._template_answer(sql, rows)  # 즉시 반환
    return await llm.complete(self._interp_prompt(sql, rows))

def _template_answer(self, sql: str, rows: list) -> str:
    if not rows:
        return "조회 결과가 없습니다."
    col_names = list(rows[0].keys())
    return f"총 {len(rows)}건 조회되었습니다. 주요 컬럼: {', '.join(col_names)}"
```

**주의사항:**
1. **모델별 프롬프트 개별 튜닝 필요** — Gemma4는 few-shot 더 필요, GPT-OSS는 system prompt에 민감. Golden Eval에서 `llm_model` 컬럼(T1 DDL에 이미 있음)으로 모델별 점수 추적.
2. **초기에는 routing 전체를 gpt-oss로** — ModelRouter 구조만 먼저 도입 후, 한 태스크씩 경량 교체하면서 Golden Eval 통과 확인. 교체 실패 시 즉시 롤백.
3. **SQL Generation은 절대 경량 교체 금지** (정확도 핵심 단계).

**테스트:** `tests/unit/test_model_router.py`
```python
def test_router_returns_correct_provider():
    router = ModelRouter(
        providers={"gpt-oss": mock_heavy, "gemma4-4b": mock_light},
        routing={"sql_generation": "gpt-oss", "schema_linking": "gemma4-4b"},
    )
    assert router.get("sql_generation") is mock_heavy
    assert router.get("schema_linking") is mock_light

def test_router_fallback_to_heavy_for_unknown_task():
    router = ModelRouter(providers=..., routing={})
    assert router.get("unknown_task") is mock_heavy  # gpt-oss 폴백
```

---

## 10. 파일 변경 요약

### T1~T7 (기초 인프라)

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

### T8~T17 (고급 최적화)

| 파일 | 변경 유형 | 태스크 |
|------|----------|--------|
| `app/core/agents/db/agent.py` | 수정 (Self-Consistency, 캐싱, Verification, AST 수정 연동) | T8, T9, T12, T17 |
| `app/core/agents/db/verification.py` | 신규 | T9 |
| `tests/unit/test_verification.py` | 신규 | T9 |
| `db/migrations/005_eval_metrics.sql` | 신규 (eval_case 컬럼 추가) | T10 |
| `tests/golden/metrics.py` | 수정 (EX / Component Match / Valid SQL Rate 추가) | T10 |
| `tests/golden/datasets/db_phase1.yaml` | 수정 (`expected_sql` 필드 추가) | T10 |
| `app/core/agents/db/schema_linker.py` | 수정 (2단계 재랭킹) | T11 |
| `config/prompts/schema_rerank.j2` | 신규 | T11 |
| `tests/unit/test_schema_linker_rerank.py` | 신규 | T11 |
| `db/migrations/006_query_log_cache.sql` | 신규 (question_hash 컬럼) | T12 |
| `app/infra/db/query_log.py` | 수정 (get_cached_sql, save_success) | T12 |
| `app/core/agents/db/value_matcher.py` | 신규 | T13 |
| `tests/unit/test_value_matcher.py` | 신규 | T13 |
| `config/few_shot/sql_antipatterns.yaml` | 신규 | T14 |
| `config/prompts/sql_gen.j2` | 수정 (anti-pattern 섹션 추가) | T14 |
| `db/migrations/007_active_learning.sql` | 신규 | T15 |
| `app/infra/db/active_learning_repo.py` | 신규 | T15 |
| `app/infra/llm/ensemble.py` | 신규 | T16 |
| `app/core/agents/db/ast_repair.py` | 신규 | T17 |
| `tests/unit/test_ast_repair.py` | 신규 | T17 |
| `requirements.txt` | 수정 (sqlglot, rapidfuzz 추가) | T10, T13, T17 |

### T18~T20 (Planner 개선 + Multi-Model)

| 파일 | 변경 유형 | 태스크 |
|------|----------|--------|
| `app/core/orchestrator/planner.py` | 수정 (prefilter, confidence scoring, 경량 LLM 분류) | T18, T19 |
| `app/core/agents/smalltalk/agent.py` | 신규 | T18 |
| `tests/unit/test_planner_prefilter.py` | 신규 | T18 |
| `config/prompts/planner.j2` | 신규 | T19 |
| `tests/unit/test_planner_confidence.py` | 신규 | T19 |
| `app/infra/llm/router.py` | 신규 | T20 |
| `config/llm_routing.yaml` | 신규 | T20 |
| `.env.example` | 수정 (LLM_MODEL_LIGHT, LLM_MODEL_HEAVY 추가) | T20 |
| `tests/unit/test_model_router.py` | 신규 | T20 |

---

## 11. 논문 활용 방법

이 구현이 완료되면:

1. **실험 재현성:** `eval_run` 테이블에 `git_sha` 기록 → 어느 커밋에서 어떤 점수였는지 추적 가능
2. **Ablation Study:** T3(ValueStore) → T4(Few-shot) → T6(CoT) → T8~T17 순서로 각각 Golden Eval 실행 → 각 개선분이 점수에 미친 영향 측정

### Ablation Study 결과 표

| 구성 | EX Score | Valid SQL % | Hard EX | Latency(ms) |
|------|----------|-------------|---------|-------------|
| Baseline (T1~T2만) | ? | ? | ? | ? |
| + ValueStore (T3) | ? | ? | ? | ? |
| + Few-shot 20개 (T4) | ? | ? | ? | ? |
| + Schema 설명 개선 (T5) | ? | ? | ? | ? |
| + Chain-of-Thought (T6) | ? | ? | ? | ? |
| + AST 수정 (T17) | ? | ? | ? | ? |
| + Fuzzy Matching (T13) | ? | ? | ? | ? |
| + Anti-pattern (T14) | ? | ? | ? | ? |
| + Schema Linker 2단계 (T11) | ? | ? | ? | ? |
| + Self-Consistency N=3 (T8) | ? | ? | ? | ? |
| + Planner Pre-filter (T18) | - | - | - | ~100ms (잡담) |
| + ModelRouter 경량 분리 (T20) | ? | ? | ? | ~8~12초 (일반) |
| Full System (모두 적용) | ? | ? | ? | ? |

> **측정 방법:** 각 구성을 별도 git 브랜치에서 Golden Eval 실행 → `eval_run.git_sha`로 추적. 논문 Table 1로 활용.

### 평가 지표 설명

| 지표 | 수식 | 의미 |
|------|------|------|
| **EX (Execution Accuracy)** | 실행 결과 집합 일치 / 전체 케이스 | 실제 DB 결과 기준 정확도 |
| **Valid SQL %** | 파싱 성공 SQL / 전체 생성 SQL | 문법 오류 없는 SQL 비율 |
| **Hard EX** | Hard 난이도 EX | 복잡한 쿼리 처리 능력 |
| **Keyword Score** | 키워드 포함 / 기대 키워드 | 경량 근사 지표 (실 DB 불필요) |
| **Component Match** | FROM/WHERE/SELECT 절 일치율 | 구조 수준 정확도 |

### 데이터 추출 쿼리

```sql
-- 전체 실험 결과 추이
SELECT r.run_id, r.git_sha, r.overall_score,
       r.passed, r.total,
       SUM(CASE WHEN c.difficulty='hard' AND c.passed=1 THEN 1 ELSE 0 END) AS hard_passed,
       COUNT(CASE WHEN c.difficulty='hard' THEN 1 END) AS hard_total,
       AVG(c.execution_accuracy) AS avg_ex,
       SUM(CASE WHEN c.from_match=1 AND c.where_match=1 THEN 1 ELSE 0 END) AS full_component_match,
       r.created_at
FROM eval_run r
JOIN eval_case c ON r.run_id = c.run_id
WHERE r.dataset = 'db_phase1'
GROUP BY r.run_id
ORDER BY r.created_at;

-- 난이도별 통과율
SELECT c.difficulty,
       COUNT(*) AS total,
       SUM(c.passed) AS passed,
       ROUND(AVG(c.score), 4) AS avg_score,
       ROUND(AVG(c.execution_accuracy), 4) AS avg_ex
FROM eval_case c
JOIN eval_run r ON c.run_id = r.run_id
WHERE r.run_id = (SELECT MAX(run_id) FROM eval_run WHERE dataset='db_phase1')
GROUP BY c.difficulty;
```
---

## 12. 외부 거장 관점 비교 평가 + 내 의견

이 섹션은 공개적으로 LLM 웹서비스 구축 원칙을 많이 드러낸 다섯 명의 관점을 기준으로, 현재 문서의 강점과 약점을 비교 평가한 내용이다.

### 12-1. 비교 대상으로 잡은 5명

1. Greg Brockman, OpenAI
2. Dario Amodei, Anthropic
3. Guillermo Rauch, Vercel
4. Harrison Chase, LangChain
5. Simon Willison, independent researcher/builder

### 12-2. 각자 중요하게 볼 기준 3개와 정량/정성 평가

| 인물 | 기준 1 | 점수 | 기준 2 | 점수 | 기준 3 | 점수 | 정성 평가 |
|------|--------|-----:|--------|-----:|--------|-----:|----------|
| Greg Brockman | 실제 제품 동작을 반영하는 eval | 9/10 | 반복 가능한 엔지니어링 프로세스 | 8/10 | 배포 후 점진적 개선 구조 | 7/10 | 이 문서는 측정, 회귀 확인, 반복 개선 관점이 강해서 Brockman식 접근과 잘 맞는다 |
| Dario Amodei | 신뢰성 / steerability | 7/10 | human feedback과 안전 장치 | 6/10 | 검증되기 전 복잡도 확대 억제 | 5/10 | 정확도 개선 방향은 좋지만 제안 기능이 빠르게 많아져 복잡도 리스크가 크다 |
| Guillermo Rauch | latency와 체감 UX | 8/10 | provider / model 교체 가능성 | 8/10 | 운영 신뢰성과 가시성 | 6/10 | pre-filter와 model routing은 좋지만 failover, 운영 visibility는 약하다 |
| Harrison Chase | trace 기반 eval과 agent 단위 측정 | 8/10 | observability와 regression 방지 | 8/10 | harness / tool interface 품질 | 7/10 | eval/CI 사고방식은 좋고, 상태/도구 인터페이스 계약은 더 구체화될 여지가 있다 |
| Simon Willison | prompt injection과 권한 경계 | 3/10 | 단순성 / 디버깅 용이성 | 6/10 | 규칙/로그/키워드 같은 단순 기법 존중 | 8/10 | 경량 라우팅은 좋지만 보안과 trust boundary 설계가 매우 약하다 |

### 12-3. 이 문서 전체에 대한 종합 점수

| 항목 | 점수 | 코멘트 |
|------|-----:|--------|
| 정확도 개선 방향 | 8.5/10 | schema, few-shot, value grounding, richer metrics 우선순위가 좋다 |
| 운영 준비도 | 7.0/10 | offline eval 기반은 좋지만 production failure loop는 아직 약하다 |
| 성능 / 응답성 | 7.5/10 | planner pre-filter와 model routing은 실전적인 개선 포인트다 |
| 보안 / 권한 경계 | 4.0/10 | prompt injection, 정책 위반 SQL 유도, 악성 입력 대응이 거의 없다 |
| 복잡도 통제 | 5.5/10 | 좋은 아이디어가 많지만 한 번에 많이 들어오면 원인 추적과 rollback이 어려워진다 |

### 12-4. 어디가 괜찮은지

이 문서의 가장 큰 장점은 개선을 프롬프트 감각 문제가 아니라 측정 문제로 다룬다는 점이다. `eval_run`, `eval_case`, baseline/regression, latency 기록, EX / Valid SQL / Component Match 같은 지표 확장은 실제 운영형 LLM 서비스 개선 방식과 잘 맞는다.

또 다른 장점은 사용자 체감 속도를 아키텍처 레벨에서 다루려는 점이다. planner pre-filter, confidence 기반 경량 분류, model routing은 웹서비스에서 바로 체감되는 개선 포인트다.

### 12-5. 어디가 약한지

가장 약한 부분은 보안과 trust-boundary 설계다. 문서는 Text-to-SQL 정확도 향상에 집중하지만, prompt injection, 금지 테이블 유도, schema/value poisoning, 애매한 정책 경계 질문에 대한 대응 설계가 거의 없다.

두 번째 약점은 복잡도 통제다. T8~T20은 각각 타당하지만 self-consistency, ensemble, AST repair, active learning, multi-model routing까지 빠르게 넣으면 어떤 변화가 실제로 성능을 올렸는지 분리하기 어려워진다.

세 번째 약점은 online feedback loop가 약하다는 점이다. golden eval은 강하지만, 실제 운영에서는 실사용 trace, 저신뢰 응답, 재시도, reviewer feedback이 다시 eval set으로 들어오는 루프가 중요하다.

### 12-6. 내가 보는 더 좋은 실행 순서

안전하게 개선하고, 무엇이 효과 있었는지 명확히 남기려면 현재 초안보다 더 강하게 우선순위를 잘라서 가는 편이 좋다.

**먼저 할 것**
- T1 eval persistence
- T3 real ValueStore loading
- T4 few-shot expansion
- T5 schema description quality management
- T10 richer evaluation metrics
- T18 planner pre-filter

**그 다음 할 것**
- T11 two-stage schema linker
- T13 fuzzy value matching
- T19 planner confidence + light-model fallback
- T20 model router

T20은 처음부터 모델을 나누지 말고, 우선 같은 모델로 routing 구조만 넣은 뒤 eval 결과가 확인되면 분리하는 것이 안전하다.

**나중에 할 것**
- T8 self-consistency
- T16 ensemble
- T17 AST repair
- T15 active learning loop

이유는 간단하다. 지금 단계에서 ROI가 가장 큰 것은 더 좋은 grounding, 더 좋은 routing, 더 좋은 measurement다. 생성 후 복잡한 보정은 그 다음이어야 한다.

### 12-7. 내가 추가로 넣고 싶은 개선안 4개

1. `security eval` 트랙 추가
   - prompt injection, forbidden-table 요청, unsafe SQL 유도, 정책 경계 질문을 별도 dataset으로 관리
   - `unsafe_sql_rate`, `policy_violation_escape_rate` 같은 지표 추가

2. full active-learning 전에 `online trace review` 루프 추가
   - 실제 `/chat` 실패, 재시도, low-confidence 응답을 샘플링
   - 주기적으로 golden dataset으로 승격

3. 단계별 latency budget 명시
   - planner `< 300ms`
   - schema linking `< 1.5s`
   - SQL generation `< 5s`
   - full turn의 P50 / P95 지속 추적

4. 단일 점수 대신 rollback guardrail 명시
   - EX가 올라가도 P95 latency가 2배면 reject
   - Valid SQL이 좋아져도 unsafe behavior가 늘면 reject
   - 개선 여부는 multi-metric으로 판단

### 12-8. 최종 의견

정확도 개선 로드맵으로 보면 이 문서는 강하다. 반면 운영형 LLM 웹서비스 로드맵으로 보면 보안과 복잡도 관리가 아직 약하다.

내 총평 점수는 **7.4/10**이다.

내가 실제로 진행한다면 T1 / T3 / T4 / T5 / T10 / T18을 먼저 끝내고, 이후 실제 trace와 ablation 결과를 보고 T11 / T13 / T19 / T20을 얼마나 채택할지 결정하겠다.

### 12-9. 참고 링크

- OpenAI, evals primer: https://openai.com/index/evals-drive-next-chapter-of-ai/
- OpenAI eval best practices: https://platform.openai.com/docs/guides/evaluation-best-practices
- Anthropic, Building Effective AI Agents: https://www.anthropic.com/research/building-effective-agents
- Anthropic, Demystifying evals for AI agents: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic eval design docs: https://docs.anthropic.com/en/docs/build-with-claude/develop-tests
- Vercel, AI SDK 5: https://vercel.com/blog/ai-sdk-5
- Vercel, AI Gateway GA: https://vercel.com/blog/ai-gateway-is-now-generally-available
- Vercel streaming guide: https://vercel.com/kb/guide/streaming-from-llm
- LangChain, Agent Evaluation Readiness Checklist: https://blog.langchain.com/agent-evaluation-readiness-checklist/
- LangChain, On Agent Frameworks and Agent Observability: https://blog.langchain.com/on-agent-frameworks-and-agent-observability/
- LangChain, Your harness, your memory: https://blog.langchain.com/your-harness-your-memory/
- Simon Willison, Prompt injection risks: https://simonwillison.net/2023/Apr/14/worst-that-can-happen/
- Simon Willison, Prompt injection vs jailbreaking: https://simonwillison.net/2024/Mar/5/prompt-injection-and-jailbreaking-are-not-the-same-thing/
- Simon Willison, practical LLM lessons: https://simonwillison.net/2024/May/29/a-year-of-building-with-llms/
