# TC VOC Chatbot Phase 1 — DB Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FastAPI 백엔드 + DB Agent (Text-to-SQL 10단계 파이프라인)로 Type 1, 2 VOC 질문에 SSE 스트리밍으로 응답.

**Architecture:** Walking Skeleton. 핵심 인터페이스(Agent ABC, LLMProvider ABC)를 먼저 잡고, DB Agent 파이프라인을 TDD로 순서대로 구현. 모든 LLM/DB 호출은 infra 계층에서 추상화.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, oracledb (thin mode), sqlglot, Jinja2, httpx, structlog, scikit-learn (TF-IDF), pytest, respx

---

## 파일 구조 전체

```
app/
├── main.py
├── config.py
├── api/
│   ├── deps.py
│   ├── middleware/tracing.py
│   └── v1/
│       ├── chat.py
│       └── feedback.py
├── core/
│   ├── agents/
│   │   ├── base.py          # Agent ABC, SubQuery, AgentResult, Evidence
│   │   ├── registry.py
│   │   └── db/
│   │       ├── agent.py     # DBAgent (파이프라인 조립)
│   │       ├── schema_linker.py
│   │       ├── sql_generator.py
│   │       ├── refiner.py
│   │       ├── interpreter.py
│   │       └── validator.py
│   ├── orchestrator/
│   │   ├── planner.py
│   │   └── executor.py
│   └── synthesizer.py
├── infra/
│   ├── llm/
│   │   ├── base.py
│   │   ├── internal_api.py
│   │   └── prompt_renderer.py
│   ├── db/
│   │   ├── oracle.py
│   │   ├── schema_store.py
│   │   ├── value_store.py
│   │   ├── few_shot_store.py
│   │   └── sessions.py
│   └── config/
│       ├── loader.py
│       └── poller.py
└── shared/
    ├── schemas.py
    ├── exceptions.py
    └── logging.py

config/
├── agents.yaml
├── thresholds.yaml
├── whitelist.yaml
├── prompts/
│   ├── schema_linker.j2
│   ├── sql_gen.j2
│   ├── sql_refiner.j2
│   └── synthesizer.j2
├── schema/tc_oracle.yaml
└── few_shot/sql_seed.yaml

db/migrations/001_initial.sql

tests/
├── conftest.py
├── unit/
│   ├── test_validator.py
│   ├── test_schema_store.py
│   └── test_skeleton.py
├── integration/
│   └── test_db_agent_flow.py
└── golden/
    ├── datasets/db_phase1.yaml
    └── runner.py
```

---

## Task 1: 프로젝트 설정

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `docker-compose.yml`

- [ ] **Step 1: pyproject.toml 작성**

```toml
[project]
name = "tc-voc-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "pydantic>=2.7",
    "pydantic-settings>=2.2",
    "httpx>=0.27",
    "jinja2>=3.1",
    "sqlglot>=23",
    "oracledb>=2.2",
    "structlog>=24",
    "scikit-learn>=1.4",
    "numpy>=1.26",
    "pyyaml>=6.0",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
    "httpx>=0.27",
    "pytest-cov>=5",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2: .env.example 작성**

```bash
# LLM
LLM_API_BASE_URL=http://internal-llm-api/v1
LLM_API_KEY=your-key-here
LLM_MODEL=gpt-oss

# Oracle App DB (세션/로그/few-shot 저장용)
APP_DB_DSN=localhost:1521/APPDB
APP_DB_USER=voc_app
APP_DB_PASSWORD=your-password

# Oracle TC DB (read-only)
TC_DB_DSN=tc-oracle-host:1521/TCDB
TC_DB_USER=voc_readonly
TC_DB_PASSWORD=your-password

# 설정
CONFIDENCE_THRESHOLD=0.7
LOG_LEVEL=INFO
```

- [ ] **Step 3: docker-compose.yml (테스트용 Oracle)**

```yaml
services:
  oracle-test:
    image: gvenzl/oracle-free:23-slim
    environment:
      ORACLE_PASSWORD: testpass
      APP_USER: voc_app
      APP_USER_PASSWORD: testpass
    ports:
      - "1521:1521"
    healthcheck:
      test: ["CMD", "healthcheck.sh"]
      interval: 10s
      timeout: 5s
      retries: 10
```

- [ ] **Step 4: 디렉토리 생성 및 의존성 설치**

```bash
mkdir -p app/{api/{middleware,v1},core/{agents/db,orchestrator},infra/{llm,db,config},shared}
mkdir -p config/prompts config/schema config/few_shot
mkdir -p db/migrations
mkdir -p tests/{unit,integration,golden/datasets}
touch app/__init__.py app/api/__init__.py app/api/middleware/__init__.py
touch app/api/v1/__init__.py app/core/__init__.py app/core/agents/__init__.py
touch app/core/agents/db/__init__.py app/core/orchestrator/__init__.py
touch app/infra/__init__.py app/infra/llm/__init__.py app/infra/db/__init__.py
touch app/infra/config/__init__.py app/shared/__init__.py
pip install -e ".[dev]"
```

Expected: 의존성 설치 완료, 디렉토리 생성 확인.

- [ ] **Step 5: Commit**

```bash
git init
git add pyproject.toml .env.example docker-compose.yml
git commit -m "feat: project scaffold"
```

---

## Task 2: 핵심 도메인 모델 + 예외 + 로깅

**Files:**
- Create: `app/shared/schemas.py`
- Create: `app/shared/exceptions.py`
- Create: `app/shared/logging.py`
- Create: `tests/unit/test_schemas.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_schemas.py
from app.shared.schemas import SubQuery, AgentResult, Evidence

def test_sub_query_defaults():
    q = SubQuery(id="t1", agent="db", query="A 설비 PARAM_X?")
    assert q.depends_on == []

def test_agent_result_requires_evidence_list():
    e = Evidence(
        id="ev1",
        source_type="db_row",
        content="PARAM_X=Y",
        metadata={"table": "PARAMETER"}
    )
    r = AgentResult(
        sub_query_id="t1",
        success=True,
        evidence=[e],
        raw_data=None,
        confidence=0.9,
        error=None,
    )
    assert r.confidence == 0.9
    assert len(r.evidence) == 1

def test_confidence_range_validated():
    import pytest
    with pytest.raises(Exception):
        AgentResult(
            sub_query_id="t1", success=True,
            evidence=[], raw_data=None, confidence=1.5, error=None
        )
```

- [ ] **Step 2: 실패 확인**

```bash
pytest tests/unit/test_schemas.py -v
```

Expected: `ImportError` — `app.shared.schemas` not found.

- [ ] **Step 3: schemas.py 구현**

```python
# app/shared/schemas.py
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class SubQuery(BaseModel):
    id: str
    agent: str
    query: str
    depends_on: list[str] = []


class Evidence(BaseModel):
    id: str
    source_type: Literal["db_row", "log_line", "doc_chunk", "knowledge_entry"]
    content: str
    metadata: dict[str, Any] = {}


class AgentResult(BaseModel):
    sub_query_id: str
    success: bool
    evidence: list[Evidence]
    raw_data: Any
    confidence: float = Field(ge=0.0, le=1.0)
    error: Optional[str] = None


class Context(BaseModel):
    session_id: str
    trace_id: str
    history: list[dict[str, str]] = []


class ChatRequest(BaseModel):
    session_id: str
    message: str
    user_id: str = "anonymous"


class FeedbackRequest(BaseModel):
    message_id: int
    rating: Literal["P", "N"]
    comment: Optional[str] = None
```

- [ ] **Step 4: exceptions.py 구현**

```python
# app/shared/exceptions.py
class VocBaseError(Exception):
    pass

class SQLValidationError(VocBaseError):
    def __init__(self, reason: str, sql: str = ""):
        self.reason = reason
        self.sql = sql
        super().__init__(f"SQL validation failed: {reason}")

class LLMError(VocBaseError):
    pass

class DBExecutionError(VocBaseError):
    pass

class ConfigError(VocBaseError):
    pass
```

- [ ] **Step 5: logging.py 구현**

```python
# app/shared/logging.py
import uuid
import structlog
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")


def new_trace_id() -> str:
    tid = str(uuid.uuid4())
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get()


def setup_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(__import__("logging"), level)
        ),
    )


def get_logger(name: str):
    return structlog.get_logger(name)
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
pytest tests/unit/test_schemas.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add app/shared/
git commit -m "feat: core schemas, exceptions, structured logging"
```

---

## Task 3: Oracle 커넥션 풀 + DDL 마이그레이션

**Files:**
- Create: `app/infra/db/oracle.py`
- Create: `db/migrations/001_initial.sql`
- Create: `tests/unit/test_oracle.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_oracle.py
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from app.infra.db.oracle import OraclePool


@pytest.mark.asyncio
async def test_fetch_all_returns_list():
    pool = OraclePool(dsn="mock", user="u", password="p")
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = [("row1",), ("row2",)]
    mock_cursor.description = [("col",)]
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch.object(pool, "_get_conn", return_value=mock_conn):
        rows = await pool.fetch_all("SELECT 1 FROM DUAL")

    assert len(rows) == 2


@pytest.mark.asyncio
async def test_fetch_all_timeout_raises():
    import asyncio
    pool = OraclePool(dsn="mock", user="u", password="p", timeout_sec=0.001)

    async def slow_query(*args, **kwargs):
        await asyncio.sleep(10)

    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.execute = slow_query
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch.object(pool, "_get_conn", return_value=mock_conn):
        with pytest.raises(Exception):
            await pool.fetch_all("SELECT 1 FROM DUAL")
```

- [ ] **Step 2: 실패 확인**

```bash
pytest tests/unit/test_oracle.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: oracle.py 구현**

```python
# app/infra/db/oracle.py
import asyncio
from contextlib import asynccontextmanager
from typing import Any
import oracledb
from app.shared.exceptions import DBExecutionError
from app.shared.logging import get_logger

logger = get_logger(__name__)


class OraclePool:
    def __init__(
        self,
        dsn: str,
        user: str,
        password: str,
        min_size: int = 2,
        max_size: int = 10,
        timeout_sec: float = 5.0,
    ):
        self.dsn = dsn
        self.user = user
        self.password = password
        self.min_size = min_size
        self.max_size = max_size
        self.timeout_sec = timeout_sec
        self._pool: oracledb.AsyncConnectionPool | None = None

    async def start(self) -> None:
        self._pool = oracledb.create_pool_async(
            user=self.user,
            password=self.password,
            dsn=self.dsn,
            min=self.min_size,
            max=self.max_size,
        )

    async def stop(self) -> None:
        if self._pool:
            await self._pool.close()

    @asynccontextmanager
    async def _get_conn(self):
        async with self._pool.acquire() as conn:
            yield conn

    async def fetch_all(
        self, sql: str, params: dict | None = None, *, max_rows: int = 1000
    ) -> list[dict[str, Any]]:
        try:
            async with asyncio.timeout(self.timeout_sec):
                async with self._get_conn() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(sql, params or {})
                        cols = [d[0].lower() for d in cur.description]
                        rows = await cur.fetchmany(max_rows)
                        return [dict(zip(cols, row)) for row in rows]
        except asyncio.TimeoutError:
            raise DBExecutionError(f"Query timed out after {self.timeout_sec}s")
        except oracledb.Error as e:
            raise DBExecutionError(str(e)) from e

    async def execute(self, sql: str, params: dict | None = None) -> None:
        """INSERT/UPDATE/DELETE 전용. 결과 fetch 없이 commit."""
        try:
            async with asyncio.timeout(self.timeout_sec):
                async with self._get_conn() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(sql, params or {})
                        await conn.commit()
        except asyncio.TimeoutError:
            raise DBExecutionError(f"Query timed out after {self.timeout_sec}s")
        except oracledb.Error as e:
            raise DBExecutionError(str(e)) from e
```

- [ ] **Step 4: DDL 마이그레이션 작성**

```sql
-- db/migrations/001_initial.sql
-- Phase 1 필수 테이블

CREATE TABLE chat_sessions (
  session_id     VARCHAR2(36) PRIMARY KEY,
  user_id        VARCHAR2(50),
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP,
  last_active_at TIMESTAMP,
  metadata       CLOB CHECK (metadata IS JSON)
);

CREATE TABLE chat_messages (
  message_id     NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id     VARCHAR2(36) REFERENCES chat_sessions(session_id),
  role           VARCHAR2(10),
  content        CLOB,
  citations      CLOB CHECK (citations IS JSON),
  confidence     NUMBER(3,2),
  trace_id       VARCHAR2(36),
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);
CREATE INDEX idx_msg_session ON chat_messages(session_id, created_at);

CREATE TABLE feedback_log (
  feedback_id    NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  message_id     NUMBER REFERENCES chat_messages(message_id),
  user_id        VARCHAR2(50),
  rating         CHAR(1),
  comment        CLOB,
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE query_log (
  query_id       NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  question_hash  VARCHAR2(64),
  question       CLOB,
  agent_used     VARCHAR2(50),
  sql_generated  CLOB,
  result_summary CLOB,
  latency_ms     NUMBER,
  cached_until   TIMESTAMP,
  trace_id       VARCHAR2(36),
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);
CREATE INDEX idx_query_hash ON query_log(question_hash, cached_until);

CREATE TABLE config_version (
  scope          VARCHAR2(50) PRIMARY KEY,
  version        NUMBER,
  updated_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE few_shot_bank (
  id                 NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  question_skeleton  CLOB,
  question_original  CLOB,
  sql_text           CLOB,
  source             VARCHAR2(20),
  hit_count          NUMBER DEFAULT 0,
  success_rate       NUMBER(3,2),
  enabled            CHAR(1) DEFAULT 'Y',
  created_at         TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- 초기 config_version 행
INSERT INTO config_version (scope, version) VALUES ('few_shot', 1);
INSERT INTO config_version (scope, version) VALUES ('overrides', 1);
COMMIT;
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
pytest tests/unit/test_oracle.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add app/infra/db/oracle.py db/migrations/
git commit -m "feat: Oracle connection pool + DDL migrations"
```

---

## Task 4: Config 시스템 (Pydantic Settings + YAML 로더)

**Files:**
- Create: `app/config.py`
- Create: `app/infra/config/loader.py`
- Create: `config/thresholds.yaml`
- Create: `config/agents.yaml`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_config.py
import pytest
from app.infra.config.loader import ConfigLoader


def test_load_thresholds(tmp_path):
    yaml_file = tmp_path / "thresholds.yaml"
    yaml_file.write_text("confidence_auto_send: 0.75\nmax_refine_attempts: 2\n")
    loader = ConfigLoader(config_dir=str(tmp_path))
    cfg = loader.load_thresholds()
    assert cfg["confidence_auto_send"] == 0.75
    assert cfg["max_refine_attempts"] == 2


def test_load_whitelist(tmp_path):
    yaml_file = tmp_path / "whitelist.yaml"
    yaml_file.write_text(
        "tables:\n  PARAMETER:\n    columns: [param_id, param_name]\n    requires_where_clause: true\n"
        "large_tables: [DCOL_LOG]\nforbidden_functions: [DBMS_]\n"
    )
    loader = ConfigLoader(config_dir=str(tmp_path))
    wl = loader.load_whitelist()
    assert "PARAMETER" in wl["tables"]
    assert wl["tables"]["PARAMETER"]["requires_where_clause"] is True
```

- [ ] **Step 2: 실패 확인**

```bash
pytest tests/unit/test_config.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: config.py (Pydantic Settings) 구현**

```python
# app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # LLM
    llm_api_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-oss"

    # Oracle App DB
    app_db_dsn: str = "localhost:1521/APPDB"
    app_db_user: str = "voc_app"
    app_db_password: str = ""

    # Oracle TC DB (read-only)
    tc_db_dsn: str = "localhost:1521/TCDB"
    tc_db_user: str = "voc_readonly"
    tc_db_password: str = ""

    # 설정
    config_dir: str = "config"
    confidence_threshold: float = 0.7
    log_level: str = "INFO"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

- [ ] **Step 4: loader.py 구현**

```python
# app/infra/config/loader.py
import yaml
from pathlib import Path
from app.shared.exceptions import ConfigError


class ConfigLoader:
    def __init__(self, config_dir: str = "config"):
        self.base = Path(config_dir)

    def _load(self, filename: str) -> dict:
        path = self.base / filename
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def load_thresholds(self) -> dict:
        return self._load("thresholds.yaml")

    def load_whitelist(self) -> dict:
        return self._load("whitelist.yaml")

    def load_schema(self) -> dict:
        return self._load("schema/tc_oracle.yaml")

    def load_few_shot_seed(self) -> list[dict]:
        data = self._load("few_shot/sql_seed.yaml")
        return data.get("examples", [])

    def load_agents(self) -> dict:
        return self._load("agents.yaml")
```

- [ ] **Step 5: YAML 파일 작성**

```yaml
# config/thresholds.yaml
confidence_auto_send: 0.70
max_refine_attempts: 2
schema_rag_top_k: 5
few_shot_top_k: 3
value_retrieval_top_n: 5
cache_ttl_db_sec: 3600
```

```yaml
# config/agents.yaml
agents:
  db:
    enabled: true
    description: "TC Oracle DB Text-to-SQL 에이전트"
  doc:
    enabled: false   # Phase 2
  log:
    enabled: false   # Phase 3
  knowledge:
    enabled: false   # Phase 4
```

```yaml
# config/whitelist.yaml
tables:
  PARAMETER:
    columns: [param_id, param_name, eqp_id, created_at]
    requires_where_clause: true
  MODEL_INFO:
    columns: [eqp_id, model_name, version, created_at]
    requires_where_clause: false
  DCOL_ITEM:
    columns: [item_id, item_name, eqp_id, dev_status, created_at]
    requires_where_clause: true
large_tables: [DCOL_LOG, EVENT_HISTORY]
forbidden_functions: ["DBMS_", "UTL_", "EXEC"]
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
pytest tests/unit/test_config.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/infra/config/loader.py config/
git commit -m "feat: config system - pydantic settings + yaml loader"
```

---

## Task 5: SQL Validator

**Files:**
- Create: `app/core/agents/db/validator.py`
- Create: `tests/unit/test_validator.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_validator.py
import pytest
from app.core.agents.db.validator import SQLValidator
from app.shared.exceptions import SQLValidationError

WHITELIST = {
    "tables": {
        "PARAMETER": {
            "columns": ["param_id", "param_name", "eqp_id"],
            "requires_where_clause": True,
        },
        "MODEL_INFO": {
            "columns": ["eqp_id", "model_name", "version"],
            "requires_where_clause": False,
        },
    },
    "large_tables": ["DCOL_LOG"],
    "forbidden_functions": ["DBMS_", "UTL_"],
}


def make_validator():
    return SQLValidator(whitelist=WHITELIST)


def test_valid_select_passes():
    v = make_validator()
    sql = v.validate_and_fix(
        "SELECT param_name FROM PARAMETER WHERE eqp_id = 'EQP_A_001'"
    )
    assert "PARAMETER" in sql


def test_delete_blocked():
    v = make_validator()
    with pytest.raises(SQLValidationError) as exc:
        v.validate_and_fix("DELETE FROM PARAMETER WHERE 1=1")
    assert "SELECT" in exc.value.reason


def test_unknown_table_blocked():
    v = make_validator()
    with pytest.raises(SQLValidationError):
        v.validate_and_fix("SELECT * FROM SECRET_TABLE")


def test_forbidden_function_blocked():
    v = make_validator()
    with pytest.raises(SQLValidationError):
        v.validate_and_fix("SELECT DBMS_OUTPUT.PUT_LINE('x') FROM DUAL")


def test_rownum_injected_when_missing():
    v = make_validator()
    sql = v.validate_and_fix(
        "SELECT param_name FROM PARAMETER WHERE eqp_id = 'EQP_A_001'"
    )
    assert "ROWNUM" in sql.upper() or "FETCH" in sql.upper()


def test_where_required_for_large_table():
    v = make_validator()
    with pytest.raises(SQLValidationError):
        v.validate_and_fix("SELECT * FROM DCOL_LOG")


def test_column_not_in_whitelist_blocked():
    v = make_validator()
    with pytest.raises(SQLValidationError):
        v.validate_and_fix(
            "SELECT secret_col FROM PARAMETER WHERE eqp_id = 'X'"
        )
```

- [ ] **Step 2: 실패 확인**

```bash
pytest tests/unit/test_validator.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: validator.py 구현**

```python
# app/core/agents/db/validator.py
import re
import sqlglot
from sqlglot import expressions as exp
from app.shared.exceptions import SQLValidationError


class SQLValidator:
    def __init__(self, whitelist: dict):
        self.whitelist = whitelist
        self.allowed_tables: set[str] = set(whitelist.get("tables", {}).keys())
        self.large_tables: set[str] = set(whitelist.get("large_tables", []))
        self.forbidden: list[str] = whitelist.get("forbidden_functions", [])

    def validate_and_fix(self, sql: str) -> str:
        sql = sql.strip().rstrip(";")

        # 1. 파싱
        try:
            tree = sqlglot.parse_one(sql, dialect="oracle")
        except Exception as e:
            raise SQLValidationError(f"SQL 파싱 실패: {e}", sql)

        # 2. SELECT만 허용
        if not isinstance(tree, exp.Select):
            raise SQLValidationError("SELECT 문만 허용됩니다", sql)

        # 3. 위험 함수 차단
        sql_upper = sql.upper()
        for fn in self.forbidden:
            pattern = fn.replace("_", r"\_").replace("*", ".*")
            if re.search(pattern, sql_upper):
                raise SQLValidationError(f"금지 함수 사용: {fn}", sql)

        # 4. 테이블 화이트리스트
        used_tables = {t.name.upper() for t in tree.find_all(exp.Table)}
        forbidden_tables = used_tables - self.allowed_tables
        if forbidden_tables:
            raise SQLValidationError(
                f"허용되지 않은 테이블: {forbidden_tables}. "
                f"허용 목록: {self.allowed_tables}",
                sql,
            )

        # 5. 컬럼 화이트리스트
        for col in tree.find_all(exp.Column):
            col_name = col.name.lower()
            table_name = col.table.upper() if col.table else None
            if table_name and table_name in self.whitelist["tables"]:
                allowed_cols = self.whitelist["tables"][table_name]["columns"]
                if col_name not in allowed_cols and col_name != "*":
                    raise SQLValidationError(
                        f"허용되지 않은 컬럼: {table_name}.{col_name}", sql
                    )

        # 6. 대용량 테이블은 WHERE 필수
        for t in used_tables:
            if t in self.large_tables:
                if not tree.find(exp.Where):
                    raise SQLValidationError(
                        f"대용량 테이블 {t}은 WHERE 절이 필요합니다", sql
                    )

        # 7. requires_where_clause
        for t in used_tables:
            tconf = self.whitelist["tables"].get(t, {})
            if tconf.get("requires_where_clause") and not tree.find(exp.Where):
                raise SQLValidationError(
                    f"테이블 {t}은 WHERE 절이 필요합니다", sql
                )

        # 8. ROWNUM 자동 주입
        if not tree.find(exp.Limit) and "ROWNUM" not in sql_upper:
            sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= 1000"

        return sql
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/unit/test_validator.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/core/agents/db/validator.py tests/unit/test_validator.py
git commit -m "feat: SQL validator with whitelist, forbidden functions, ROWNUM injection"
```

---

## Task 6: Schema Store (TF-IDF 기반 in-memory)

**Files:**
- Create: `app/infra/db/schema_store.py`
- Create: `config/schema/tc_oracle.yaml`
- Create: `tests/unit/test_schema_store.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_schema_store.py
import pytest
from app.infra.db.schema_store import SchemaStore

SAMPLE_SCHEMA = {
    "tables": {
        "PARAMETER": {
            "description": "설비별 파라미터 정의 마스터",
            "columns": {
                "param_name": {"type": "VARCHAR2", "description": "파라미터 명칭"},
                "eqp_id": {"type": "VARCHAR2", "description": "설비 ID"},
            },
            "relationships": ["PARAMETER.eqp_id = MODEL_INFO.eqp_id"],
        },
        "MODEL_INFO": {
            "description": "설비 모델 정보 테이블",
            "columns": {
                "eqp_id": {"type": "VARCHAR2", "description": "설비 ID"},
                "model_name": {"type": "VARCHAR2", "description": "모델명"},
            },
            "relationships": [],
        },
    }
}


def test_search_returns_relevant_tables():
    store = SchemaStore()
    store.load(SAMPLE_SCHEMA)
    results = store.search("설비 파라미터 기능", top_k=2)
    table_names = [r["table"] for r in results]
    assert "PARAMETER" in table_names


def test_search_top_k_limits_results():
    store = SchemaStore()
    store.load(SAMPLE_SCHEMA)
    results = store.search("설비", top_k=1)
    assert len(results) == 1


def test_format_for_prompt():
    store = SchemaStore()
    store.load(SAMPLE_SCHEMA)
    results = store.search("파라미터", top_k=1)
    formatted = store.format_for_prompt(results)
    assert "PARAMETER" in formatted
    assert "param_name" in formatted
```

- [ ] **Step 2: 실패 확인**

```bash
pytest tests/unit/test_schema_store.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: schema_store.py 구현**

```python
# app/infra/db/schema_store.py
from __future__ import annotations
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class SchemaStore:
    def __init__(self):
        self._schema: dict = {}
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        self._table_names: list[str] = []

    def load(self, schema: dict) -> None:
        self._schema = schema
        tables = schema.get("tables", {})
        self._table_names = list(tables.keys())

        docs = []
        for name, tconf in tables.items():
            col_texts = " ".join(
                f"{cn} {cd.get('description', '')} {cd.get('glossary_hint', '')}"
                for cn, cd in tconf.get("columns", {}).items()
            )
            doc = f"{name} {tconf.get('description', '')} {col_texts}"
            docs.append(doc)

        self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        self._matrix = self._vectorizer.fit_transform(docs)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if self._vectorizer is None:
            return []
        q_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(q_vec, self._matrix).flatten()
        top_idx = np.argsort(scores)[::-1][:top_k]
        tables = self._schema.get("tables", {})
        results = []
        for idx in top_idx:
            name = self._table_names[idx]
            results.append({"table": name, "score": float(scores[idx]), "config": tables[name]})
        return results

    def format_for_prompt(self, results: list[dict]) -> str:
        lines = []
        for r in results:
            name = r["table"]
            conf = r["config"]
            lines.append(f"테이블: {name} — {conf.get('description', '')}")
            for col, cconf in conf.get("columns", {}).items():
                hint = cconf.get("glossary_hint", "")
                lines.append(f"  - {col} ({cconf.get('type','')}) : {cconf.get('description','')} {hint}".strip())
            for rel in conf.get("relationships", []):
                lines.append(f"  관계: {rel}")
        return "\n".join(lines)
```

- [ ] **Step 4: tc_oracle.yaml 작성**

```yaml
# config/schema/tc_oracle.yaml
tables:
  PARAMETER:
    description: "설비별 파라미터 정의 마스터. 한 설비당 다수 행."
    columns:
      param_id:
        type: NUMBER
        description: "파라미터 고유 ID"
      param_name:
        type: VARCHAR2
        description: "파라미터 명칭. 'PARAM_*' 형식."
      eqp_id:
        type: VARCHAR2
        description: "설비 ID"
        glossary_hint: "사용자가 'A 설비'라고 하면 EQP_A_* 패턴으로 검색"
      created_at:
        type: TIMESTAMP
        description: "생성 일시"
    relationships:
      - "PARAMETER.eqp_id = MODEL_INFO.eqp_id"

  MODEL_INFO:
    description: "설비 모델 정보. 설비당 1행."
    columns:
      eqp_id:
        type: VARCHAR2
        description: "설비 고유 ID. 'EQP_<영역>_<번호>' 형식."
        glossary_hint: "사용자가 '설비' 또는 장비명으로 질문할 때 이 컬럼으로 JOIN"
      model_name:
        type: VARCHAR2
        description: "모델명"
      version:
        type: VARCHAR2
        description: "TC 버전"
      created_at:
        type: TIMESTAMP
        description: "등록 일시"
    relationships: []

  DCOL_ITEM:
    description: "설비별 개발 완료된 기능(DCOL) 항목. 기능 개발 이력 확인용."
    columns:
      item_id:
        type: NUMBER
        description: "항목 고유 ID"
      item_name:
        type: VARCHAR2
        description: "기능명. 'DCOL_*' 형식."
      eqp_id:
        type: VARCHAR2
        description: "설비 ID"
      dev_status:
        type: VARCHAR2
        description: "개발 상태. 'DONE'/'IN_PROGRESS'/'PENDING'"
      created_at:
        type: TIMESTAMP
        description: "개발 완료 일시"
    relationships:
      - "DCOL_ITEM.eqp_id = MODEL_INFO.eqp_id"
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
pytest tests/unit/test_schema_store.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/infra/db/schema_store.py config/schema/ tests/unit/test_schema_store.py
git commit -m "feat: schema store with TF-IDF in-memory search"
```

---

## Task 7: Value Store (trigram 기반 값 후보 검색)

**Files:**
- Create: `app/infra/db/value_store.py`
- Create: `tests/unit/test_value_store.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_value_store.py
from app.infra.db.value_store import ValueStore


def test_find_candidates_for_eqp():
    store = ValueStore()
    store.load_values("eqp_id", ["EQP_A_001", "EQP_A_002", "EQP_B_001"])
    hits = store.find_candidates("A 설비", top_n=2)
    # A 설비 → EQP_A_* 가 상위에 와야 함
    assert any("EQP_A" in h for h in hits)


def test_find_candidates_exact_match():
    store = ValueStore()
    store.load_values("param_name", ["PARAM_TEMP", "PARAM_PRESSURE", "PARAM_FLOW"])
    hits = store.find_candidates("PARAM_TEMP", top_n=3)
    assert "PARAM_TEMP" in hits


def test_extract_candidates_from_question():
    store = ValueStore()
    store.load_values("eqp_id", ["EQP_A_001", "EQP_B_001"])
    store.load_values("param_name", ["PARAM_X", "PARAM_Y"])
    result = store.extract_from_question("A 설비에 PARAM_X 있어?")
    assert len(result) > 0
```

- [ ] **Step 2: 실패 확인**

```bash
pytest tests/unit/test_value_store.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: value_store.py 구현**

```python
# app/infra/db/value_store.py
from __future__ import annotations
import difflib
import re


def _trigrams(s: str) -> set[str]:
    s = s.lower()
    return {s[i:i+3] for i in range(len(s) - 2)}


class ValueStore:
    def __init__(self):
        self._index: dict[str, list[str]] = {}  # column_name -> [values]

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
        results = []
        for v in all_values:
            overlap = len(term_tg & _trigrams(v))
            if overlap > 0:
                results.append((overlap, v))
        results.sort(reverse=True)
        return [v for _, v in results[:top_n]]

    def extract_from_question(self, question: str) -> dict[str, list[str]]:
        tokens = re.findall(r"[A-Z_0-9]{3,}|[가-힣]+", question.upper())
        result: dict[str, list[str]] = {}
        for token in tokens:
            candidates = self.find_candidates(token, top_n=3)
            if candidates:
                result[token] = candidates
        return result
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/unit/test_value_store.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/infra/db/value_store.py tests/unit/test_value_store.py
git commit -m "feat: value store with trigram fuzzy matching"
```

---

## Task 8: LLM Provider + Prompt 렌더러

**Files:**
- Create: `app/infra/llm/base.py`
- Create: `app/infra/llm/internal_api.py`
- Create: `app/infra/llm/prompt_renderer.py`
- Create: `config/prompts/schema_linker.j2`
- Create: `config/prompts/sql_gen.j2`
- Create: `config/prompts/sql_refiner.j2`
- Create: `config/prompts/synthesizer.j2`
- Create: `tests/unit/test_prompt_renderer.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_prompt_renderer.py
import pytest
from app.infra.llm.prompt_renderer import PromptRenderer


def test_render_sql_gen_template(tmp_path):
    tpl = tmp_path / "sql_gen.j2"
    tpl.write_text(
        "스키마:\n{{ schema }}\n질문: {{ question }}\n"
        "{% for ex in few_shots %}예시: {{ ex.question }} -> {{ ex.sql }}\n{% endfor %}"
    )
    renderer = PromptRenderer(prompt_dir=str(tmp_path))
    result = renderer.render(
        "sql_gen",
        schema="TABLE_A col1",
        question="A 설비?",
        few_shots=[{"question": "Q", "sql": "SELECT 1"}],
    )
    assert "TABLE_A" in result
    assert "SELECT 1" in result


def test_missing_template_raises(tmp_path):
    renderer = PromptRenderer(prompt_dir=str(tmp_path))
    with pytest.raises(Exception):
        renderer.render("nonexistent")
```

- [ ] **Step 2: 실패 확인**

```bash
pytest tests/unit/test_prompt_renderer.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: base.py 구현**

```python
# app/infra/llm/base.py
from abc import ABC, abstractmethod
from typing import AsyncIterator


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, prompt: str, **kwargs) -> str: ...

    @abstractmethod
    async def stream(self, prompt: str, **kwargs) -> AsyncIterator[str]: ...

    @abstractmethod
    async def complete_json(self, prompt: str, schema: dict | None = None, **kwargs) -> dict: ...
```

- [ ] **Step 4: internal_api.py 구현**

```python
# app/infra/llm/internal_api.py
from __future__ import annotations
import json
from typing import AsyncIterator
import httpx
from app.infra.llm.base import LLMProvider
from app.shared.exceptions import LLMError
from app.shared.logging import get_logger

logger = get_logger(__name__)


class InternalLLMProvider(LLMProvider):
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def complete(self, prompt: str, **kwargs) -> str:
        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": kwargs.get("temperature", 0.0),
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            raise LLMError(f"LLM API error: {e}") from e

    async def stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "temperature": kwargs.get("temperature", 0.0),
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    data = json.loads(line[6:])
                    delta = data["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta

    async def complete_json(self, prompt: str, schema: dict | None = None, **kwargs) -> dict:
        result = await self.complete(
            prompt + "\n\n반드시 JSON만 출력하세요. 코드블록 없이.",
            temperature=0.0,
            **kwargs,
        )
        try:
            cleaned = result.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM이 유효한 JSON을 반환하지 않음: {e}\n원본: {result}") from e
```

- [ ] **Step 5: prompt_renderer.py 구현**

```python
# app/infra/llm/prompt_renderer.py
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from app.shared.exceptions import ConfigError


class PromptRenderer:
    def __init__(self, prompt_dir: str = "config/prompts"):
        self._env = Environment(
            loader=FileSystemLoader(prompt_dir),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template_name: str, **kwargs) -> str:
        try:
            tpl = self._env.get_template(f"{template_name}.j2")
            return tpl.render(**kwargs)
        except TemplateNotFound:
            raise ConfigError(f"Prompt template not found: {template_name}.j2")
```

- [ ] **Step 6: Jinja2 프롬프트 템플릿 작성**

```jinja2
{# config/prompts/schema_linker.j2 #}
당신은 Oracle DB 전문가입니다. 사용자 질문에 필요한 테이블과 컬럼을 식별하세요.

[사용 가능한 스키마]
{{ schema_context }}

[질문]
{{ question }}

관련 테이블, 컬럼, JOIN 조건을 JSON으로 출력하세요.
{
  "tables": ["테이블명"],
  "columns": ["테이블.컬럼명"],
  "joins": ["TABLE_A.col = TABLE_B.col"]
}
```

```jinja2
{# config/prompts/sql_gen.j2 #}
당신은 Oracle SQL 전문가입니다. 단계별로 사고한 뒤 SQL을 생성하세요.

[관련 스키마]
{{ schema_subset }}

{% if few_shots %}
[유사 예시]
{% for ex in few_shots %}
질문: {{ ex.question }}
SQL: {{ ex.sql }}
{% endfor %}
{% endif %}

{% if value_candidates %}
[값 후보]
{% for term, candidates in value_candidates.items() %}
- "{{ term }}" → {{ candidates }}
{% endfor %}
{% endif %}

[질문]
{{ question }}

[추론 단계]
1. 어떤 테이블이 필요한가?
2. 어떤 컬럼이 필요한가?
3. JOIN 조건은?
4. WHERE 조건과 사용할 값은?
5. 결과를 어떻게 제한할 것인가?

[출력 — JSON만]
{
  "reasoning": "단계별 요약",
  "sql": "Oracle SQL 문",
  "confidence": 0.0,
  "assumptions": []
}
```

```jinja2
{# config/prompts/sql_refiner.j2 #}
이전에 생성한 SQL에서 오류가 발생했습니다. 수정하세요.

[원본 질문]
{{ question }}

[이전 SQL]
{{ previous_sql }}

[오류 유형]
{{ error_type }}

[오류 내용]
{{ error_message }}

[수정 지침]
{{ refinement_hint }}

[허용 테이블]
{{ allowed_tables }}

수정된 SQL을 JSON으로 출력하세요.
{
  "reasoning": "수정 이유",
  "sql": "수정된 Oracle SQL",
  "confidence": 0.0
}
```

```jinja2
{# config/prompts/synthesizer.j2 #}
다음 SQL 실행 결과를 바탕으로 사용자 질문에 답하세요.

[규칙]
1. 모든 주장에 [row_N] 형식의 인용을 반드시 포함
2. SQL 결과에 없는 내용은 절대 추가하지 말 것
3. 결과가 0건이면 "확인되지 않습니다"로 답할 것

[질문]
{{ question }}

[실행한 SQL]
{{ sql }}

[결과] (총 {{ row_count }}건)
{% for row in rows %}
[row_{{ loop.index }}] {{ row }}
{% endfor %}

[출력 — JSON만]
{
  "answer": "인용 포함 답변 [row_1]...",
  "confidence": 0.0,
  "needs_human_review": false,
  "missing_info": []
}
```

- [ ] **Step 7: 테스트 통과 확인**

```bash
pytest tests/unit/test_prompt_renderer.py -v
```

Expected: 2 passed.

- [ ] **Step 8: Commit**

```bash
git add app/infra/llm/ config/prompts/ tests/unit/test_prompt_renderer.py
git commit -m "feat: LLM provider interface + internal API + Jinja2 prompt renderer"
```

---

## Task 9: Few-shot Store (YAML 시드 + DB 누적)

**Files:**
- Create: `app/infra/db/few_shot_store.py`
- Create: `config/few_shot/sql_seed.yaml`
- Create: `tests/unit/test_few_shot_store.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_few_shot_store.py
from app.infra.db.few_shot_store import FewShotStore, extract_skeleton

def test_extract_skeleton_eqp_param():
    s = extract_skeleton("A 설비에 PARAM_X 있나?", known_eqps=["EQP_A_001"], known_params=["PARAM_X"])
    assert "<EQP>" in s
    assert "<PARAM>" in s

def test_extract_skeleton_no_match():
    s = extract_skeleton("기능이 뭐야?", known_eqps=[], known_params=[])
    assert s == "기능이 뭐야?"

def test_store_search_by_skeleton():
    store = FewShotStore()
    store.add_seed([
        {"question": "A 설비에 PARAM_X 있나?", "sql": "SELECT 1 FROM PARAMETER WHERE eqp_id='EQP_A_001' AND param_name='PARAM_X'"},
        {"question": "B 설비에 PARAM_Y 있나?", "sql": "SELECT 1 FROM PARAMETER WHERE eqp_id='EQP_B_001' AND param_name='PARAM_Y'"},
    ])
    results = store.search("C 설비에 PARAM_Z 있어?", top_k=2)
    assert len(results) > 0
    assert "sql" in results[0]
```

- [ ] **Step 2: 실패 확인**

```bash
pytest tests/unit/test_few_shot_store.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: few_shot_store.py 구현**

```python
# app/infra/db/few_shot_store.py
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
```

- [ ] **Step 4: YAML 시드 작성**

```yaml
# config/few_shot/sql_seed.yaml
examples:
  - question: "A 설비에 PARAM_TEMP 기능 있나?"
    sql: "SELECT COUNT(*) AS cnt FROM PARAMETER WHERE eqp_id = 'EQP_A_001' AND param_name = 'PARAM_TEMP' AND ROWNUM <= 1"

  - question: "A 설비와 B 설비의 파라미터 차이가 뭐야?"
    sql: |
      SELECT a.param_name, CASE WHEN b.param_name IS NULL THEN 'B에 없음' ELSE '동일' END AS status
      FROM PARAMETER a
      LEFT JOIN PARAMETER b ON a.param_name = b.param_name AND b.eqp_id = 'EQP_B_001'
      WHERE a.eqp_id = 'EQP_A_001' AND ROWNUM <= 100

  - question: "A 설비의 DCOL_ITEM 목록 알려줘"
    sql: "SELECT item_name, dev_status FROM DCOL_ITEM WHERE eqp_id = 'EQP_A_001' AND ROWNUM <= 100"

  - question: "MODEL_INFO에서 A 설비 버전이 뭐야?"
    sql: "SELECT version FROM MODEL_INFO WHERE eqp_id = 'EQP_A_001'"

  - question: "A 설비에 개발 완료된 기능이 몇 개야?"
    sql: "SELECT COUNT(*) AS cnt FROM DCOL_ITEM WHERE eqp_id = 'EQP_A_001' AND dev_status = 'DONE'"
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
pytest tests/unit/test_few_shot_store.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/infra/db/few_shot_store.py config/few_shot/ tests/unit/test_few_shot_store.py
git commit -m "feat: few-shot store with skeleton extraction and TF-IDF search"
```

---

## Task 10: Agent 인터페이스 + DB Agent 하위 모듈 (Schema Linker, SQL Generator, Refiner, Interpreter)

**Files:**
- Create: `app/core/agents/base.py`
- Create: `app/core/agents/registry.py`
- Create: `app/core/agents/db/schema_linker.py`
- Create: `app/core/agents/db/sql_generator.py`
- Create: `app/core/agents/db/refiner.py`
- Create: `app/core/agents/db/interpreter.py`
- Create: `tests/integration/test_db_agent_flow.py`

- [ ] **Step 1: Agent 인터페이스 구현**

```python
# app/core/agents/base.py
from abc import ABC, abstractmethod
from app.shared.schemas import SubQuery, AgentResult, Context


class Agent(ABC):
    name: str = ""

    @abstractmethod
    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult: ...
```

```python
# app/core/agents/registry.py
from app.core.agents.base import Agent

AGENT_REGISTRY: dict[str, type[Agent]] = {}


def register(cls: type[Agent]) -> type[Agent]:
    AGENT_REGISTRY[cls.name] = cls
    return cls
```

- [ ] **Step 2: Schema Linker 구현**

```python
# app/core/agents/db/schema_linker.py
from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.schema_store import SchemaStore
from app.shared.exceptions import LLMError


class SchemaLinker:
    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        schema_store: SchemaStore,
        top_k: int = 5,
    ):
        self.llm = llm
        self.renderer = renderer
        self.schema_store = schema_store
        self.top_k = top_k

    async def link(self, question: str) -> dict:
        results = self.schema_store.search(question, top_k=self.top_k)
        schema_context = self.schema_store.format_for_prompt(results)
        prompt = self.renderer.render("schema_linker", schema_context=schema_context, question=question)
        return await self.llm.complete_json(prompt)
```

- [ ] **Step 3: SQL Generator 구현**

```python
# app/core/agents/db/sql_generator.py
from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.few_shot_store import FewShotStore
from app.infra.db.value_store import ValueStore


class SQLGenerator:
    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        few_shot_store: FewShotStore,
        value_store: ValueStore,
        few_shot_top_k: int = 3,
    ):
        self.llm = llm
        self.renderer = renderer
        self.few_shot_store = few_shot_store
        self.value_store = value_store
        self.few_shot_top_k = few_shot_top_k

    async def generate(self, question: str, schema_subset: str, linked: dict) -> dict:
        few_shots = self.few_shot_store.search(question, top_k=self.few_shot_top_k)
        value_candidates = self.value_store.extract_from_question(question)
        prompt = self.renderer.render(
            "sql_gen",
            schema_subset=schema_subset,
            question=question,
            few_shots=few_shots,
            value_candidates=value_candidates,
        )
        return await self.llm.complete_json(prompt)
```

- [ ] **Step 4: Refiner 구현**

```python
# app/core/agents/db/refiner.py
from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer


REFINEMENT_HINTS = {
    "syntax_error": "Oracle SQL 문법 오류를 수정하세요. 특히 Oracle 고유 문법(ROWNUM, NVL, TO_DATE 등)을 확인하세요.",
    "empty_result": "WHERE 조건이 너무 좁을 수 있습니다. 조건을 완화하거나 LIKE 패턴을 사용해보세요.",
    "too_many_rows": "결과가 너무 많습니다. GROUP BY 또는 추가 WHERE 조건으로 집계하세요.",
    "validation_error": "허용되지 않은 테이블 또는 컬럼을 사용했습니다. 허용 목록만 사용하세요.",
}


class SQLRefiner:
    def __init__(self, llm: LLMProvider, renderer: PromptRenderer, max_attempts: int = 2):
        self.llm = llm
        self.renderer = renderer
        self.max_attempts = max_attempts

    async def refine(
        self,
        question: str,
        previous_sql: str,
        error_type: str,
        error_message: str,
        allowed_tables: list[str],
    ) -> dict:
        hint = REFINEMENT_HINTS.get(error_type, "오류를 분석하고 SQL을 수정하세요.")
        prompt = self.renderer.render(
            "sql_refiner",
            question=question,
            previous_sql=previous_sql,
            error_type=error_type,
            error_message=error_message,
            refinement_hint=hint,
            allowed_tables=", ".join(allowed_tables),
        )
        return await self.llm.complete_json(prompt)
```

- [ ] **Step 5: Interpreter 구현**

```python
# app/core/agents/db/interpreter.py
from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer


class ResultInterpreter:
    def __init__(self, llm: LLMProvider, renderer: PromptRenderer, max_rows_in_prompt: int = 20):
        self.llm = llm
        self.renderer = renderer
        self.max_rows_in_prompt = max_rows_in_prompt

    async def interpret(self, question: str, sql: str, rows: list[dict]) -> dict:
        prompt = self.renderer.render(
            "synthesizer",
            question=question,
            sql=sql,
            rows=rows[: self.max_rows_in_prompt],
            row_count=len(rows),
        )
        return await self.llm.complete_json(prompt)
```

- [ ] **Step 6: 통합 테스트 작성 (Mock LLM)**

```python
# tests/integration/test_db_agent_flow.py
import pytest
import respx
import httpx
import json
from unittest.mock import AsyncMock, patch, MagicMock
from app.core.agents.db.schema_linker import SchemaLinker
from app.core.agents.db.sql_generator import SQLGenerator
from app.core.agents.db.validator import SQLValidator
from app.core.agents.db.refiner import SQLRefiner
from app.core.agents.db.interpreter import ResultInterpreter
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.schema_store import SchemaStore
from app.infra.db.few_shot_store import FewShotStore
from app.infra.db.value_store import ValueStore

WHITELIST = {
    "tables": {
        "PARAMETER": {"columns": ["param_id", "param_name", "eqp_id"], "requires_where_clause": True}
    },
    "large_tables": [],
    "forbidden_functions": ["DBMS_"],
}

SAMPLE_SCHEMA = {
    "tables": {
        "PARAMETER": {
            "description": "설비 파라미터",
            "columns": {"param_name": {"type": "VARCHAR2", "description": "파라미터명"}, "eqp_id": {"type": "VARCHAR2", "description": "설비ID"}},
            "relationships": [],
        }
    }
}


@pytest.fixture
def llm(tmp_path):
    return InternalLLMProvider(base_url="http://mock-llm", api_key="key", model="test")


@pytest.fixture
def renderer(tmp_path):
    (tmp_path / "schema_linker.j2").write_text("schema: {{ schema_context }}\nq: {{ question }}")
    (tmp_path / "sql_gen.j2").write_text("schema: {{ schema_subset }}\nq: {{ question }}")
    (tmp_path / "synthesizer.j2").write_text("q: {{ question }}\nsql: {{ sql }}\nrows: {{ rows }}")
    return PromptRenderer(prompt_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_schema_linker_returns_tables(llm, renderer):
    store = SchemaStore()
    store.load(SAMPLE_SCHEMA)
    linker = SchemaLinker(llm, renderer, store)

    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"tables":["PARAMETER"],"columns":["PARAMETER.param_name"],"joins":[]}'}}]},
            )
        )
        result = await linker.link("A 설비에 PARAM_X 있나?")

    assert "PARAMETER" in result["tables"]


@pytest.mark.asyncio
async def test_sql_generator_produces_sql(llm, renderer):
    fs = FewShotStore()
    vs = ValueStore()
    gen = SQLGenerator(llm, renderer, fs, vs)

    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"reasoning":"test","sql":"SELECT param_name FROM PARAMETER WHERE eqp_id=\'EQP_A_001\'","confidence":0.9,"assumptions":[]}'}}]},
            )
        )
        result = await gen.generate("A 설비 파라미터?", "PARAMETER...", {})

    assert "sql" in result
    assert "SELECT" in result["sql"].upper()


@pytest.mark.asyncio
async def test_validator_blocks_bad_sql(llm, renderer):
    v = SQLValidator(whitelist=WHITELIST)
    with pytest.raises(Exception):
        v.validate_and_fix("DELETE FROM PARAMETER")


@pytest.mark.asyncio
async def test_refiner_called_on_syntax_error(llm, renderer):
    refiner = SQLRefiner(llm, renderer)
    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"reasoning":"fixed","sql":"SELECT param_name FROM PARAMETER WHERE eqp_id=\'X\' AND ROWNUM<=1000","confidence":0.8}'}}]},
            )
        )
        result = await refiner.refine(
            question="A 설비?",
            previous_sql="SELCT * FOM PARAMETER",
            error_type="syntax_error",
            error_message="ORA-00923: FROM keyword not found",
            allowed_tables=["PARAMETER"],
        )
    assert "sql" in result
```

- [ ] **Step 7: 통합 테스트 실행**

```bash
pytest tests/integration/test_db_agent_flow.py -v
```

Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add app/core/agents/ tests/integration/test_db_agent_flow.py
git commit -m "feat: agent interfaces, schema linker, SQL generator, refiner, interpreter"
```

---

## Task 11: DB Agent (파이프라인 전체 조립)

**Files:**
- Create: `app/core/agents/db/agent.py`
- Modify: `app/core/agents/registry.py`

- [ ] **Step 1: DB Agent 구현**

```python
# app/core/agents/db/agent.py
from __future__ import annotations
import hashlib
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.core.agents.db.schema_linker import SchemaLinker
from app.core.agents.db.sql_generator import SQLGenerator
from app.core.agents.db.validator import SQLValidator
from app.core.agents.db.refiner import SQLRefiner
from app.core.agents.db.interpreter import ResultInterpreter
from app.infra.db.oracle import OraclePool
from app.infra.db.schema_store import SchemaStore
from app.infra.db.few_shot_store import FewShotStore
from app.shared.schemas import SubQuery, AgentResult, Evidence, Context
from app.shared.exceptions import SQLValidationError, DBExecutionError
from app.shared.logging import get_logger

logger = get_logger(__name__)


@register
class DBAgent(Agent):
    name = "db"

    def __init__(
        self,
        linker: SchemaLinker,
        generator: SQLGenerator,
        validator: SQLValidator,
        refiner: SQLRefiner,
        interpreter: ResultInterpreter,
        tc_pool: OraclePool,
        few_shot_store: FewShotStore,
        schema_store: SchemaStore,
        max_refine: int = 2,
        confidence_threshold: float = 0.7,
    ):
        self.linker = linker
        self.generator = generator
        self.validator = validator
        self.refiner = refiner
        self.interpreter = interpreter
        self.tc_pool = tc_pool
        self.few_shot_store = few_shot_store
        self.schema_store = schema_store
        self.max_refine = max_refine
        self.confidence_threshold = confidence_threshold

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        question = sub_query.query
        log = logger.bind(trace_id=context.trace_id, agent="db")

        # 1. Schema Linking
        log.info("schema_linking_start")
        linked = await self.linker.link(question)
        results = self.schema_store.search(question, top_k=5)
        schema_subset = self.schema_store.format_for_prompt(
            [r for r in results if r["table"] in linked.get("tables", [])] or results[:3]
        )

        # 2~5. SQL 생성 + 검증 + 실행 (refine loop)
        sql_result = await self.generator.generate(question, schema_subset, linked)
        sql = sql_result.get("sql", "")
        gen_confidence = sql_result.get("confidence", 0.0)
        rows: list[dict] = []
        error_type = ""
        error_msg = ""

        for attempt in range(self.max_refine + 1):
            try:
                validated_sql = self.validator.validate_and_fix(sql)
                rows = await self.tc_pool.fetch_all(validated_sql)

                if not rows and attempt < self.max_refine:
                    log.info("empty_result_refine", attempt=attempt)
                    refined = await self.refiner.refine(
                        question, sql, "empty_result", "결과 0건", list(self.validator.allowed_tables)
                    )
                    sql = refined.get("sql", sql)
                    continue
                break

            except SQLValidationError as e:
                error_type = "validation_error"
                error_msg = str(e)
                if attempt >= self.max_refine:
                    return AgentResult(
                        sub_query_id=sub_query.id,
                        success=False,
                        evidence=[],
                        raw_data=None,
                        confidence=0.0,
                        error=error_msg,
                    )
                refined = await self.refiner.refine(
                    question, sql, error_type, error_msg, list(self.validator.allowed_tables)
                )
                sql = refined.get("sql", sql)

            except DBExecutionError as e:
                error_type = "syntax_error"
                error_msg = str(e)
                if attempt >= self.max_refine:
                    return AgentResult(
                        sub_query_id=sub_query.id,
                        success=False,
                        evidence=[],
                        raw_data=None,
                        confidence=0.0,
                        error=error_msg,
                    )
                refined = await self.refiner.refine(
                    question, sql, error_type, error_msg, list(self.validator.allowed_tables)
                )
                sql = refined.get("sql", sql)

        # 9. Result Interpretation
        interp = await self.interpreter.interpret(question, sql, rows)
        answer = interp.get("answer", "")
        confidence = min(gen_confidence, interp.get("confidence", gen_confidence))

        evidences = [
            Evidence(
                id=f"row_{i+1}",
                source_type="db_row",
                content=str(row),
                metadata={"sql": sql, "row_index": i},
            )
            for i, row in enumerate(rows)
        ]

        # 10. Success Cache
        if confidence >= self.confidence_threshold and rows:
            self.few_shot_store.add_success(question, sql)
            log.info("few_shot_cached", skeleton=question[:50])

        return AgentResult(
            sub_query_id=sub_query.id,
            success=True,
            evidence=evidences,
            raw_data={"sql": sql, "rows": rows, "answer": answer},
            confidence=confidence,
        )
```

- [ ] **Step 2: 단위 테스트 추가 (integration 파일에 추가)**

```python
# tests/integration/test_db_agent_flow.py 하단에 추가
from app.core.agents.db.agent import DBAgent
from app.shared.schemas import SubQuery, Context
import uuid


@pytest.mark.asyncio
async def test_db_agent_full_pipeline_success(llm, renderer):
    schema_store = SchemaStore()
    schema_store.load(SAMPLE_SCHEMA)
    few_shot = FewShotStore()
    value_store = ValueStore()
    linker = SchemaLinker(llm, renderer, schema_store)
    gen = SQLGenerator(llm, renderer, few_shot, value_store)
    validator = SQLValidator(whitelist=WHITELIST)
    refiner = SQLRefiner(llm, renderer)
    interpreter = ResultInterpreter(llm, renderer)

    mock_pool = AsyncMock()
    mock_pool.fetch_all = AsyncMock(return_value=[{"param_name": "PARAM_X", "eqp_id": "EQP_A_001"}])

    agent = DBAgent(
        linker=linker, generator=gen, validator=validator,
        refiner=refiner, interpreter=interpreter,
        tc_pool=mock_pool, few_shot_store=few_shot,
        schema_store=schema_store,
    )

    with respx.mock:
        # 1. schema_linker 응답
        respx.post("http://mock-llm/chat/completions").mock(
            side_effect=[
                httpx.Response(200, json={"choices": [{"message": {"content": '{"tables":["PARAMETER"],"columns":["PARAMETER.param_name"],"joins":[]}'}}]}),
                httpx.Response(200, json={"choices": [{"message": {"content": '{"reasoning":"ok","sql":"SELECT param_name FROM PARAMETER WHERE eqp_id=\'EQP_A_001\' AND param_name=\'PARAM_X\'","confidence":0.9,"assumptions":[]}'}}]}),
                httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"EQP_A_001에 PARAM_X가 존재합니다[row_1]","confidence":0.9,"needs_human_review":false,"missing_info":[]}'}}]}),
            ]
        )
        result = await agent.run(
            SubQuery(id="t1", agent="db", query="A 설비에 PARAM_X 있나?"),
            Context(session_id="s1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert result.confidence > 0.5
    assert len(result.evidence) > 0
```

- [ ] **Step 3: 테스트 실행**

```bash
pytest tests/integration/test_db_agent_flow.py -v
```

Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add app/core/agents/db/agent.py
git commit -m "feat: DB Agent full pipeline (schema linking → SQL gen → validate → execute → interpret → cache)"
```

---

## Task 12: FastAPI App + SSE 채팅 엔드포인트

**Files:**
- Create: `app/main.py`
- Create: `app/api/middleware/tracing.py`
- Create: `app/api/deps.py`
- Create: `app/api/v1/chat.py`
- Create: `app/api/v1/feedback.py`
- Create: `app/infra/db/sessions.py`

- [ ] **Step 1: tracing 미들웨어**

```python
# app/api/middleware/tracing.py
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.shared.logging import new_trace_id
import structlog


class TracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        tid = request.headers.get("X-Trace-Id") or new_trace_id()
        structlog.contextvars.bind_contextvars(trace_id=tid)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        structlog.contextvars.clear_contextvars()
        return response
```

- [ ] **Step 2: session 관리**

```python
# app/infra/db/sessions.py
from __future__ import annotations
import json
from app.infra.db.oracle import OraclePool
from app.shared.logging import get_logger

logger = get_logger(__name__)


class SessionRepository:
    def __init__(self, app_pool: OraclePool):
        self.pool = app_pool

    async def get_or_create(self, session_id: str, user_id: str) -> dict:
        rows = await self.pool.fetch_all(
            "SELECT session_id FROM chat_sessions WHERE session_id = :sid",
            {"sid": session_id},
        )
        if not rows:
            await self.pool.execute(
                "INSERT INTO chat_sessions (session_id, user_id) VALUES (:sid, :uid)",
                {"sid": session_id, "uid": user_id},
            )
        return {"session_id": session_id, "user_id": user_id}

    async def save_message(self, session_id: str, role: str, content: str,
                           citations: list, confidence: float, trace_id: str) -> int:
        await self.pool.execute(
            "INSERT INTO chat_messages (session_id, role, content, citations, confidence, trace_id)"
            " VALUES (:sid, :role, :content, :citations, :conf, :tid)",
            {
                "sid": session_id, "role": role, "content": content,
                "citations": json.dumps(citations), "conf": confidence, "tid": trace_id,
            },
        )
        rows = await self.pool.fetch_all(
            "SELECT MAX(message_id) AS mid FROM chat_messages"
            " WHERE session_id = :sid AND trace_id = :tid",
            {"sid": session_id, "tid": trace_id},
        )
        return rows[0].get("mid", 0) if rows else 0

    async def get_history(self, session_id: str, limit: int = 10) -> list[dict]:
        return await self.pool.fetch_all(
            "SELECT role, content FROM chat_messages WHERE session_id = :sid "
            "ORDER BY created_at DESC FETCH FIRST :lim ROWS ONLY",
            {"sid": session_id, "lim": limit},
        )
```

- [ ] **Step 3: deps.py (의존성 주입)**

```python
# app/api/deps.py
from functools import lru_cache
from app.config import get_settings
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.infra.db.oracle import OraclePool
from app.infra.db.schema_store import SchemaStore
from app.infra.db.value_store import ValueStore
from app.infra.db.few_shot_store import FewShotStore
from app.infra.db.sessions import SessionRepository
from app.infra.config.loader import ConfigLoader
from app.core.agents.db.schema_linker import SchemaLinker
from app.core.agents.db.sql_generator import SQLGenerator
from app.core.agents.db.validator import SQLValidator
from app.core.agents.db.refiner import SQLRefiner
from app.core.agents.db.interpreter import ResultInterpreter
from app.core.agents.db.agent import DBAgent

_db_agent: DBAgent | None = None
_session_repo: SessionRepository | None = None


async def get_db_agent() -> DBAgent:
    return _db_agent


async def get_session_repo() -> SessionRepository:
    return _session_repo


async def init_dependencies() -> None:
    global _db_agent, _session_repo
    s = get_settings()
    loader = ConfigLoader(s.config_dir)
    thresholds = loader.load_thresholds()
    whitelist = loader.load_whitelist()
    schema_data = loader.load_schema()
    seed_data = loader.load_few_shot_seed()

    llm = InternalLLMProvider(s.llm_api_base_url, s.llm_api_key, s.llm_model)
    renderer = PromptRenderer(f"{s.config_dir}/prompts")

    schema_store = SchemaStore()
    schema_store.load(schema_data)

    value_store = ValueStore()
    few_shot_store = FewShotStore()
    few_shot_store.add_seed(seed_data)

    validator = SQLValidator(whitelist=whitelist)

    tc_pool = OraclePool(s.tc_db_dsn, s.tc_db_user, s.tc_db_password)
    await tc_pool.start()

    app_pool = OraclePool(s.app_db_dsn, s.app_db_user, s.app_db_password)
    await app_pool.start()

    _session_repo = SessionRepository(app_pool)
    _db_agent = DBAgent(
        linker=SchemaLinker(llm, renderer, schema_store, thresholds.get("schema_rag_top_k", 5)),
        generator=SQLGenerator(llm, renderer, few_shot_store, value_store, thresholds.get("few_shot_top_k", 3)),
        validator=validator,
        refiner=SQLRefiner(llm, renderer, thresholds.get("max_refine_attempts", 2)),
        interpreter=ResultInterpreter(llm, renderer),
        tc_pool=tc_pool,
        few_shot_store=few_shot_store,
        schema_store=schema_store,
        max_refine=thresholds.get("max_refine_attempts", 2),
        confidence_threshold=thresholds.get("confidence_auto_send", 0.7),
    )
```

- [ ] **Step 4: chat.py (SSE 엔드포인트)**

```python
# app/api/v1/chat.py
import json
import uuid
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.shared.schemas import ChatRequest, SubQuery, Context
from app.api.deps import get_db_agent, get_session_repo
from app.core.agents.db.agent import DBAgent
from app.infra.db.sessions import SessionRepository
from app.shared.logging import get_trace_id, get_logger

router = APIRouter()
logger = get_logger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream(req: ChatRequest, agent: DBAgent, session_repo: SessionRepository):
    trace_id = get_trace_id()
    ctx = Context(session_id=req.session_id, trace_id=trace_id)

    await session_repo.get_or_create(req.session_id, req.user_id)
    await session_repo.save_message(req.session_id, "user", req.message, [], 1.0, trace_id)

    yield _sse("plan", {"agent": "db", "status": "DB 조회 중..."})

    sub_query = SubQuery(id=str(uuid.uuid4()), agent="db", query=req.message)
    result = await agent.run(sub_query, ctx)

    if not result.success:
        yield _sse("error", {"message": result.error or "오류가 발생했습니다"})
        yield _sse("done", {})
        return

    answer = result.raw_data.get("answer", "") if result.raw_data else ""
    citations = [e.model_dump() for e in result.evidence]

    for chunk in answer.split(" "):
        yield _sse("token", {"text": chunk + " "})

    yield _sse("citation", {"citations": citations})
    yield _sse("confidence", {"score": result.confidence, "needs_review": result.confidence < 0.7})

    msg_id = await session_repo.save_message(
        req.session_id, "assistant", answer, citations, result.confidence, trace_id
    )
    yield _sse("done", {"message_id": msg_id})


@router.post("/chat")
async def chat(
    req: ChatRequest,
    agent: DBAgent = Depends(get_db_agent),
    session_repo: SessionRepository = Depends(get_session_repo),
) -> StreamingResponse:
    return StreamingResponse(_stream(req, agent, session_repo), media_type="text/event-stream")
```

- [ ] **Step 5: feedback.py**

```python
# app/api/v1/feedback.py
from fastapi import APIRouter, Depends
from app.shared.schemas import FeedbackRequest
from app.infra.db.sessions import SessionRepository
from app.api.deps import get_session_repo

router = APIRouter()


@router.post("/feedback")
async def feedback(
    req: FeedbackRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
):
    await session_repo.pool.execute(
        "INSERT INTO feedback_log (message_id, rating, comment) VALUES (:mid, :rating, :comment)",
        {"mid": req.message_id, "rating": req.rating, "comment": req.comment},
    )
    return {"status": "ok"}
```

- [ ] **Step 6: main.py**

```python
# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.middleware.tracing import TracingMiddleware
from app.api.v1 import chat, feedback
from app.api.deps import init_dependencies
from app.shared.logging import setup_logging
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    await init_dependencies()
    yield


app = FastAPI(title="TC VOC Chatbot", lifespan=lifespan)
app.add_middleware(TracingMiddleware)
app.include_router(chat.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 7: 서버 기동 확인 (실제 DB 없이 import 확인)**

```bash
python -c "from app.main import app; print('import OK')"
```

Expected: `import OK`

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/api/ app/infra/db/sessions.py
git commit -m "feat: FastAPI app with SSE chat endpoint and feedback endpoint"
```

---

## Task 13: Golden Dataset + 평가 러너

**Files:**
- Create: `tests/golden/datasets/db_phase1.yaml`
- Create: `tests/golden/runner.py`
- Create: `tests/golden/metrics.py`

- [ ] **Step 1: Golden Dataset 작성 (30개)**

```yaml
# tests/golden/datasets/db_phase1.yaml
baseline_score: null  # Phase 1 완료 시 채워짐
examples:
  # === EASY (15개) ===
  - id: db_001
    difficulty: easy
    question: "A 설비에 PARAM_TEMP 기능 있나?"
    expected:
      answer_must_contain: ["PARAM_TEMP"]
      sql_must_filter_on: ["eqp_id", "param_name"]
      citation_required: true

  - id: db_002
    difficulty: easy
    question: "EQP_B_001 설비의 모델명이 뭐야?"
    expected:
      answer_must_contain: ["model_name"]
      sql_must_use_table: "MODEL_INFO"
      citation_required: true

  - id: db_003
    difficulty: easy
    question: "A 설비의 TC 버전이 뭐야?"
    expected:
      answer_must_contain: ["version"]
      sql_must_use_table: "MODEL_INFO"
      citation_required: true

  - id: db_004
    difficulty: easy
    question: "A 설비에 개발 완료된 기능 목록 알려줘"
    expected:
      sql_must_use_table: "DCOL_ITEM"
      sql_must_filter_on: ["eqp_id", "dev_status"]
      citation_required: true

  - id: db_005
    difficulty: easy
    question: "EQP_A_001에 PARAM_PRESSURE 파라미터 있어?"
    expected:
      sql_must_filter_on: ["eqp_id", "param_name"]
      citation_required: true

  - id: db_006
    difficulty: easy
    question: "A 설비 파라미터 개수가 몇 개야?"
    expected:
      sql_must_contain: ["COUNT"]
      citation_required: true

  - id: db_007
    difficulty: easy
    question: "DCOL_ITEM에서 A 설비 개발 중인 항목 알려줘"
    expected:
      sql_must_filter_on: ["eqp_id", "dev_status"]
      citation_required: true

  - id: db_008
    difficulty: easy
    question: "MODEL_INFO에서 A 설비 정보 전체 보여줘"
    expected:
      sql_must_use_table: "MODEL_INFO"
      sql_must_filter_on: ["eqp_id"]
      citation_required: true

  - id: db_009
    difficulty: easy
    question: "A 설비에 DCOL_X 기능 개발 완료됐어?"
    expected:
      sql_must_filter_on: ["eqp_id", "item_name", "dev_status"]
      citation_required: true

  - id: db_010
    difficulty: easy
    question: "EQP_A_001 설비 언제 등록됐어?"
    expected:
      sql_must_filter_on: ["eqp_id"]
      answer_must_contain: ["created_at"]
      citation_required: true

  - id: db_011
    difficulty: easy
    question: "A 설비 파라미터 목록 보여줘"
    expected:
      sql_must_use_table: "PARAMETER"
      sql_must_filter_on: ["eqp_id"]
      citation_required: true

  - id: db_012
    difficulty: easy
    question: "PARAM_FLOW라는 파라미터가 어느 설비에 있어?"
    expected:
      sql_must_filter_on: ["param_name"]
      citation_required: true

  - id: db_013
    difficulty: easy
    question: "A 설비 완료 기능 수 세줘"
    expected:
      sql_must_contain: ["COUNT"]
      sql_must_filter_on: ["dev_status"]
      citation_required: true

  - id: db_014
    difficulty: easy
    question: "EQP_B_001의 파라미터 종류가 몇 가지야?"
    expected:
      sql_must_contain: ["COUNT"]
      sql_must_filter_on: ["eqp_id"]
      citation_required: true

  - id: db_015
    difficulty: easy
    question: "A 설비 버전 정보 알려줘"
    expected:
      sql_must_use_table: "MODEL_INFO"
      citation_required: true

  # === MEDIUM (10개) ===
  - id: db_016
    difficulty: medium
    question: "A 설비와 B 설비의 파라미터 차이가 뭐야?"
    expected:
      sql_must_use_table: "PARAMETER"
      sql_must_contain: ["JOIN"]
      citation_required: true

  - id: db_017
    difficulty: medium
    question: "A 설비에는 있고 B 설비에는 없는 파라미터가 뭐야?"
    expected:
      sql_must_contain: ["NOT IN", "NOT EXISTS", "LEFT JOIN"]
      citation_required: true

  - id: db_018
    difficulty: medium
    question: "A 설비와 B 설비의 DCOL_ITEM이 동일한가요?"
    expected:
      sql_must_use_table: "DCOL_ITEM"
      citation_required: true

  - id: db_019
    difficulty: medium
    question: "모든 설비 중 PARAM_TEMP가 없는 설비는?"
    expected:
      sql_must_filter_on: ["param_name"]
      sql_must_contain: ["NOT", "LEFT JOIN"]
      citation_required: true

  - id: db_020
    difficulty: medium
    question: "A 설비의 파라미터 중 B 설비에도 있는 것만 보여줘"
    expected:
      sql_must_use_table: "PARAMETER"
      sql_must_contain: ["JOIN", "IN", "EXISTS"]
      citation_required: true

  - id: db_021
    difficulty: medium
    question: "DCOL_ITEM이 가장 많은 설비는?"
    expected:
      sql_must_contain: ["COUNT", "GROUP BY", "ORDER BY"]
      citation_required: true

  - id: db_022
    difficulty: medium
    question: "A 설비의 DCOL과 파라미터 수를 같이 보여줘"
    expected:
      sql_must_contain: ["JOIN", "COUNT"]
      citation_required: true

  - id: db_023
    difficulty: medium
    question: "최근에 추가된 파라미터 10개 알려줘"
    expected:
      sql_must_filter_on: ["created_at"]
      sql_must_contain: ["ORDER BY"]
      citation_required: true

  - id: db_024
    difficulty: medium
    question: "A 설비와 C 설비의 버전 비교해줘"
    expected:
      sql_must_use_table: "MODEL_INFO"
      sql_must_contain: ["JOIN", "WHERE", "IN"]
      citation_required: true

  - id: db_025
    difficulty: medium
    question: "파라미터가 10개 이상인 설비 목록"
    expected:
      sql_must_contain: ["COUNT", "GROUP BY", "HAVING"]
      citation_required: true

  # === HARD (5개) ===
  - id: db_026
    difficulty: hard
    question: "A 설비에만 있고 B, C 설비에는 없는 파라미터는?"
    expected:
      sql_must_contain: ["NOT"]
      citation_required: true

  - id: db_027
    difficulty: hard
    question: "DCOL 개발 완료율이 가장 높은 설비 상위 3개"
    expected:
      sql_must_contain: ["COUNT", "GROUP BY", "ORDER BY"]
      citation_required: true

  - id: db_028
    difficulty: hard
    question: "모든 설비에 공통으로 있는 파라미터는?"
    expected:
      sql_must_contain: ["GROUP BY", "COUNT", "HAVING"]
      citation_required: true

  - id: db_029
    difficulty: hard
    question: "A 설비의 파라미터 중 다른 어떤 설비에도 없는 고유 파라미터는?"
    expected:
      sql_must_contain: ["NOT", "EXISTS", "IN"]
      citation_required: true

  - id: db_030
    difficulty: hard
    question: "파라미터 수와 DCOL 완료 수를 설비별로 비교한 표 만들어줘"
    expected:
      sql_must_contain: ["JOIN", "COUNT", "GROUP BY"]
      citation_required: true
```

- [ ] **Step 2: metrics.py 구현**

```python
# tests/golden/metrics.py
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

    # SQL 검사
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

    # 답변 검사
    for term in expected.get("answer_must_contain", []):
        if term.lower() not in actual_answer.lower():
            failures.append(f"답변에 '{term}' 없음")
        scores.append(term.lower() in actual_answer.lower())

    # Citation
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
```

- [ ] **Step 3: runner.py 구현**

```python
# tests/golden/runner.py
import asyncio
import json
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
    import sys
    print("Golden eval runner — import this module and call run_golden_eval(agent, path)")
```

- [ ] **Step 4: pytest golden 테스트 추가**

```python
# tests/golden/test_golden_regression.py
import pytest
import yaml
from pathlib import Path

DATASET = Path("tests/golden/datasets/db_phase1.yaml")


def test_golden_dataset_is_valid():
    with open(DATASET) as f:
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
    baseline = yaml.safe_load(open(DATASET))["baseline_score"]
    if baseline is not None:
        assert report["overall_score"] >= baseline - 0.05, (
            f"Regression! {report['overall_score']:.2f} vs baseline {baseline:.2f}\n"
            f"Failures: {report['failures']}"
        )
```

- [ ] **Step 5: 테스트 실행 (구조 검증만)**

```bash
pytest tests/golden/test_golden_regression.py::test_golden_dataset_is_valid -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/golden/ 
git commit -m "feat: golden dataset (30 cases) + evaluation runner + regression gate"
```

---

## Task 14: Config Poller (30초 폴링 기반 리로드)

**Files:**
- Create: `app/infra/config/poller.py`

- [ ] **Step 1: poller.py 구현**

```python
# app/infra/config/poller.py
import asyncio
from app.infra.db.oracle import OraclePool
from app.shared.logging import get_logger

logger = get_logger(__name__)


class ConfigPoller:
    def __init__(self, app_pool: OraclePool, interval_sec: int = 30):
        self.pool = app_pool
        self.interval = interval_sec
        self._versions: dict[str, int] = {}
        self._callbacks: dict[str, list] = {}

    def on_change(self, scope: str, callback) -> None:
        self._callbacks.setdefault(scope, []).append(callback)

    async def start(self) -> None:
        asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                rows = await self.pool.fetch_all(
                    "SELECT scope, version FROM config_version"
                )
                for row in rows:
                    scope = row["scope"]
                    version = row["version"]
                    if self._versions.get(scope) != version:
                        self._versions[scope] = version
                        for cb in self._callbacks.get(scope, []):
                            try:
                                await cb()
                                logger.info("config_reloaded", scope=scope, version=version)
                            except Exception as e:
                                logger.error("config_reload_failed", scope=scope, error=str(e))
            except Exception as e:
                logger.error("config_poll_failed", error=str(e))
            await asyncio.sleep(self.interval)
```

- [ ] **Step 2: Commit**

```bash
git add app/infra/config/poller.py
git commit -m "feat: config poller (30s polling, no Redis)"
```

---

## Task 15: conftest.py 정리 + 전체 테스트 통과 확인

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: conftest.py 작성**

```python
# tests/conftest.py
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "real_llm: mark test as requiring real LLM API")


@pytest.fixture
def sample_whitelist():
    return {
        "tables": {
            "PARAMETER": {"columns": ["param_id", "param_name", "eqp_id"], "requires_where_clause": True},
            "MODEL_INFO": {"columns": ["eqp_id", "model_name", "version"], "requires_where_clause": False},
            "DCOL_ITEM": {"columns": ["item_id", "item_name", "eqp_id", "dev_status"], "requires_where_clause": True},
        },
        "large_tables": ["DCOL_LOG"],
        "forbidden_functions": ["DBMS_", "UTL_"],
    }
```

- [ ] **Step 2: 전체 유닛/통합 테스트 실행**

```bash
pytest tests/unit tests/integration -v --tb=short
```

Expected: 전체 통과. 실패 시 오류 메시지 확인 후 수정.

- [ ] **Step 3: Phase 1 완료 baseline 기록**

```bash
# baseline_score를 db_phase1.yaml에 기록 (실제 LLM 환경에서 측정)
# 측정 후 수동으로 db_phase1.yaml의 baseline_score 업데이트
echo "baseline_score 측정 필요 — 실제 LLM/DB 환경에서 run_golden_eval 실행 후 기록"
```

- [ ] **Step 4: 최종 Commit**

```bash
git add tests/conftest.py
git commit -m "feat: Phase 1 complete — DB Agent + FastAPI SSE + Golden Dataset"
git tag phase1-complete
```

---

## Self-Review

**Spec coverage 체크:**

| 스펙 요구사항 | 구현 Task |
|------------|---------|
| Agent ABC 인터페이스 | Task 10 |
| LLMProvider ABC | Task 8 |
| Schema RAG (TF-IDF) | Task 6 |
| Value Retrieval (trigram) | Task 7 |
| Few-shot Bank (skeleton) | Task 9 |
| Schema Linking (별도 LLM 호출) | Task 10 |
| SQL Generation (CoT + few-shot) | Task 10 |
| SQL Static Validation (sqlglot) | Task 5 |
| Oracle Execution (read-only, 5s timeout) | Task 3 |
| Refiner (에러 타입별) | Task 10 |
| Result Interpretation (citation 강제) | Task 10 |
| Success Cache (few-shot 자동 누적) | Task 11 |
| SSE 스트리밍 API | Task 12 |
| Feedback 엔드포인트 | Task 12 |
| Session/Message 저장 | Task 12 |
| Oracle DDL (5 테이블) | Task 3 |
| YAML 설정 3계층 | Task 4 |
| Jinja2 프롬프트 템플릿 | Task 8 |
| Config Poller (30초) | Task 14 |
| trace_id 구조화 로깅 | Task 2 |
| 화이트리스트 (whitelist.yaml) | Task 4 |
| Golden Dataset (30개) | Task 13 |
| 회귀 테스트 게이트 | Task 13 |
| 5계층 테스트 피라미드 | Task 2~13 전체 |

**Placeholder 스캔:** 없음. 모든 코드 블록 완성.

**Type 일관성:**
- `SubQuery`, `AgentResult`, `Evidence`, `Context` — Task 2에서 정의, Task 10~12에서 동일하게 사용.
- `OraclePool.fetch_all` — Task 3에서 정의, Task 11, 12에서 동일 시그니처로 사용.
- `Agent.run(sub_query: SubQuery, context: Context) -> AgentResult` — Task 10에서 정의, Task 11에서 동일하게 구현.
- `LLMProvider.complete_json` — Task 8에서 정의, Task 10 각 하위 모듈에서 동일하게 호출.

---

Plan complete and saved to `docs/superpowers/plans/2026-04-18-phase1-db-agent.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — 각 Task를 독립 서브에이전트가 실행, Task 완료 후 리뷰, 빠른 반복

**2. Inline Execution** — 현재 세션에서 executing-plans 스킬로 배치 실행, 체크포인트 리뷰

**어느 방식으로 진행할까요?**
