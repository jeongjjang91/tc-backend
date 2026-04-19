# TC VOC Chatbot — AI Agent 개발 가이드

> 이 문서는 사내 AI Agent(Claude Code, OpenCode 등)가 이 프로젝트를 이해하고
> Phase 2~4를 이어서 개발할 수 있도록 작성된 상세 온보딩 가이드입니다.

## 목차

0. [Quickstart — 일단 돌려보기](#0-quickstart--일단-돌려보기)
1. [프로젝트 배경](#1-프로젝트-배경)
2. [현재 구현 상태 (Phase 1 완료)](#2-현재-구현-상태-phase-1-완료)
3. [핵심 아키텍처 패턴](#3-핵심-아키텍처-패턴)
4. [핵심 타입 (반드시 숙지)](#4-핵심-타입-반드시-숙지)
5. [LLM 사용 원칙 (매우 중요)](#5-llm-사용-원칙-매우-중요)
6. [LLMProvider 사용 패턴](#6-llmprovider-사용-패턴)
7. [프롬프트 템플릿 (Jinja2)](#7-프롬프트-템플릿-jinja2)
8. [테스트 전략](#8-테스트-전략)
9. [사내 시스템 연동 포인트](#9-사내-시스템-연동-포인트)
10. [실행 커맨드 치트시트](#10-실행-커맨드-치트시트)
11. [API 사용 예시](#11-api-사용-예시)
12. [Phase 2~4 구현 가이드](#12-phase-24-구현-가이드)
13. [Phase 2 시작 워크플로우 (실전)](#13-phase-2-시작-워크플로우-실전)
14. [코딩 컨벤션](#14-코딩-컨벤션)
15. [흔한 에러와 해결](#15-흔한-에러와-해결)
16. [자주 하는 실수](#16-자주-하는-실수)

---

## 0. Quickstart — 일단 돌려보기

사내에서 레포를 처음 받았을 때 순서대로 진행:

```bash
# 1. 레포 클론
git clone https://github.com/jeongjjang91/tc-backend.git
cd tc-backend

# 2. 가상환경 + 패키지
python -m venv .venv
# Linux/Mac
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -e ".[dev]"

# 3. 환경변수 (.env 작성, 아래 섹션 9 참고)
cp .env.example .env
# vim .env  # 실제 값 기입

# 4. Oracle 마이그레이션 (DBA에게 요청하거나 직접)
sqlplus voc_app/비번@APPDB @db/migrations/001_initial.sql

# 5. 유닛 테스트 — DB/LLM 없이 통과해야 함
pytest tests/unit -v
# Windows: py -3 -m pytest tests/unit -v

# 6. 통합 테스트 — Mock 기반, 이것도 통과해야 함
pytest tests/integration -v

# 7. 실제 서버 실행
uvicorn app.main:app --reload --port 8000
# curl http://localhost:8000/health → {"status": "ok"}
```

**Phase 1 통과 기준:** unit + integration 테스트 100% 통과, `/health` 200 OK, `/api/v1/chat`에 "A 설비에 PARAM_X 있나?" 보냈을 때 SSE 이벤트 수신.

---

## 1. 프로젝트 배경

TC 시스템 운영팀이 반복적으로 받는 VOC를 LLM + RAG + Text-to-SQL로 자동/반자동 처리.
프론트(Vue.js SSE 챗봇)와 이 백엔드(FastAPI)가 SSE로 통신.

### VOC 4개 유형

| # | 질문 예시 | 처리 방식 | Phase |
|---|-----------|-----------|-------|
| 1 | "A 설비에 PARAM_X 있나?" | DB Text-to-SQL | Phase 1 ✅ |
| 2 | "A/B 설비 파라미터 차이?" | DB Text-to-SQL | Phase 1 ✅ |
| 3 | "A 설비 오동작 원인?" | Splunk 로그 분석 | Phase 3 |
| 4 | "PARAM_X 기능 설명해줘" | RAG (Confluence) | Phase 2 |

---

## 2. 현재 구현 상태 (Phase 1 완료)

### 완성된 것

```
app/
├── api/v1/chat.py          POST /api/v1/chat (SSE 스트리밍)
├── api/v1/feedback.py      POST /api/v1/feedback
├── api/deps.py             FastAPI 의존성 주입
├── api/middleware/tracing.py  trace_id 자동 주입
├── core/
│   ├── agents/
│   │   ├── base.py         Agent ABC (모든 Agent의 부모)
│   │   ├── registry.py     AGENT_REGISTRY (플러그인 등록)
│   │   └── db/             DBAgent 전체 구현 (10단계 파이프라인)
│   └── orchestrator/       ← 비어있음 (Phase 2~4에서 구현)
├── infra/
│   ├── llm/                LLMProvider ABC + InternalLLMProvider (httpx)
│   ├── db/                 OraclePool, SchemaStore, ValueStore, FewShotStore
│   └── config/             ConfigLoader (YAML), ConfigPoller (30초 폴링)
└── shared/
    ├── schemas.py          SubQuery, AgentResult, Evidence, Context, ChatRequest
    ├── exceptions.py       VocBaseError 계층
    └── logging.py          structlog + trace_id (ContextVar)
```

### 미완성 (Phase 2~4 대상)

```
app/core/orchestrator/planner.py    질문 유형 분류 → Agent 선택
app/core/orchestrator/executor.py   Agent 병렬 실행
app/core/synthesizer.py             복합 결과 통합
app/core/agents/rag/                RAGAgent (Phase 2)
app/core/agents/log/                SplunkAgent (Phase 3)
app/core/agents/knowledge/          KnowledgeAgent (Phase 4)
app/infra/rag/                      Confluence API 어댑터 (Phase 2)
app/infra/splunk/                   Splunk API 어댑터 (Phase 3)
```

---

## 3. 핵심 아키텍처 패턴

### 3-1. Agent 플러그인 패턴

새 Agent를 만드는 방법은 항상 동일합니다:

```python
# app/core/agents/rag/agent.py
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.shared.schemas import SubQuery, AgentResult, Context

@register                    # ← 이 데코레이터 하나로 자동 등록
class RAGAgent(Agent):
    name = "doc"             # ← config/agents.yaml의 키와 일치해야 함

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        # 구현
        ...
```

`@register` 하나만 붙이면 `AGENT_REGISTRY["doc"]`에 자동 등록됩니다.

### 3-2. 인프라 어댑터 패턴

외부 시스템(Oracle, LLM, Splunk, Confluence)은 반드시 `app/infra/` 아래에 격리합니다.
`app/core/`는 `app/infra/`에 의존하지만, 반대는 안 됩니다.

```python
# 좋은 예 — core는 인터페이스만 의존
class RAGAgent(Agent):
    def __init__(self, rag_client: RAGClient, ...):  # 인터페이스 주입
        self.rag = rag_client

# 나쁜 예 — core가 외부 시스템에 직접 의존
class RAGAgent(Agent):
    async def run(self, ...):
        resp = await httpx.get("http://confluence/...")  # ❌ infra 직접 호출
```

### 3-3. SSE 이벤트 계약

프론트와의 SSE 계약. 절대 변경하지 말 것:

```
event: plan       {"agent": "db", "status": "DB 조회 중..."}
event: token      {"text": "답변 토큰..."}
event: citation   {"citations": [...]}
event: confidence {"score": 0.9, "needs_review": false}
event: done       {"message_id": 123}
event: error      {"message": "오류 내용"}
```

### 3-4. 설정 3계층

| 계층 | 위치 | 용도 | 변경 방법 |
|------|------|------|-----------|
| Code | `app/` | Agent 로직, 인터페이스 | PR + 배포 |
| YAML | `config/` | 프롬프트, 화이트리스트, 임계값 | PR + 재시작 |
| DB | Oracle `few_shot_bank` 등 | few-shot, 임계값 오버라이드 | 운영 중 핫리로드 |

---

## 4. 핵심 타입 (반드시 숙지)

```python
# app/shared/schemas.py

class SubQuery(BaseModel):
    id: str
    agent: str          # "db" | "doc" | "log" | "knowledge"
    query: str
    depends_on: list[str] = []   # 병렬 실행 시 의존성

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
    confidence: float = Field(ge=0.0, le=1.0)  # 0~1 사이만 허용
    error: Optional[str] = None

class Context(BaseModel):
    session_id: str
    trace_id: str        # 모든 LLM 호출에 이 ID를 로그에 남길 것
    history: list[dict[str, str]] = []
```

---

## 5. LLM 사용 원칙 (매우 중요)

### ❌ 하면 안 되는 것

```python
# 정확한 출력 일치 테스트
assert result.answer == "PARAM_X가 존재합니다"  # ❌ LLM은 비결정적

# 프롬프트 파일 테스트 없이 수정
# config/prompts/*.j2 수정 → Golden Eval 없이 머지 ❌
```

### ✅ 해야 하는 것

```python
# 속성/계약 검증
assert "PARAM_X" in result.answer           # ✅ 키워드 포함
assert result.confidence >= 0.0             # ✅ 범위 검증
assert re.search(r"\[row_\d+\]", answer)   # ✅ 인용 형식
assert "SELECT" in result.sql.upper()       # ✅ SQL 구조

# 프롬프트 수정 시 Golden Eval 통과 필수
# pytest -m real_llm → overall_score >= baseline - 0.05
```

---

## 6. LLMProvider 사용 패턴

`app/infra/llm/base.py`의 `LLMProvider`는 3가지 메서드를 제공합니다. Phase 2+의 새 Agent도 이것을 통해 LLM을 호출하세요.

### 6-1. `complete(prompt) -> str`
일반 텍스트 응답. 가장 단순한 경로.

```python
answer = await self.llm.complete(
    self.renderer.render("rag_answer", question=q, chunks=chunks)
)
```

### 6-2. `stream(prompt) -> AsyncIterator[str]`
SSE 토큰 스트리밍용. Agent 내부가 아닌 API 레이어에서 주로 사용.

```python
async for token in llm.stream(prompt):
    yield sse("token", {"text": token})
```

### 6-3. `complete_json(prompt, schema=None) -> dict`
구조화된 출력이 필요할 때 (Planner의 agent 선택, SQL 생성 결과 파싱 등).
**내부에서 `temperature=0.0` 고정 + "반드시 JSON만 출력" 강제 suffix 주입 + 코드블록 제거**까지 처리합니다.

```python
# Phase 4 Orchestrator 예시
result = await self.llm.complete_json(
    self.renderer.render("planner", message=user_msg)
)
# result = {"agents": ["db", "doc"], "sub_queries": [...]}
```

**주의:** `complete_json`은 LLM이 JSON을 뱉지 못하면 `LLMError`를 던집니다. 항상 try/except로 감싸고 fallback 경로를 준비하세요.

### 6-4. 의존성 주입 패턴

Agent는 `LLMProvider`를 직접 생성하지 말고 주입받습니다. `app/api/deps.py`의 `init_dependencies()`에서 모든 의존성을 조립합니다.

```python
# ❌ 나쁜 예
class RAGAgent(Agent):
    def __init__(self):
        self.llm = InternalLLMProvider(...)  # infra 직접 생성 금지

# ✅ 좋은 예
class RAGAgent(Agent):
    def __init__(self, llm: LLMProvider, renderer: PromptRenderer, rag_client: RAGClient):
        self.llm = llm
        self.renderer = renderer
        self.rag = rag_client
```

---

## 7. 프롬프트 템플릿 (Jinja2)

모든 프롬프트는 `config/prompts/*.j2` 파일로 관리. 코드에 프롬프트 문자열을 하드코딩하지 마세요.

### 7-1. 기존 템플릿 위치
```
config/prompts/
├── schema_linker.j2     스키마 선택 (DBAgent)
├── sql_gen.j2           SQL 생성 (DBAgent, Few-shot + CoT)
├── sql_refiner.j2       SQL 에러 수정 (DBAgent)
└── synthesizer.j2       결과 요약 (DBAgent)
```

### 7-2. 새 템플릿 추가 방법

```jinja2
{# config/prompts/rag_answer.j2 #}
당신은 TC 시스템 문서 전문가입니다.

### 질문
{{ question }}

### 참고 문서
{% for chunk in chunks %}
[doc_{{ loop.index0 }}] {{ chunk.title }}
{{ chunk.content }}
---
{% endfor %}

위 문서를 바탕으로 답변하세요. 각 주장 끝에 `[doc_N]` 형식으로 인용 표시를 반드시 포함하세요.
```

### 7-3. 코드에서 렌더링

```python
from app.infra.llm.prompt_renderer import PromptRenderer

renderer = PromptRenderer("config/prompts")  # 보통 의존성 주입으로 받음
prompt = renderer.render(
    "rag_answer",         # 파일명에서 .j2 빼고
    question="PARAM_X 기능?",
    chunks=top_chunks,
)
answer = await llm.complete(prompt)
```

### 7-4. 프롬프트 수정 룰

**프롬프트 파일 수정은 코드 수정과 동급의 리뷰 대상입니다:**

1. 브랜치에서 수정 → 커밋
2. `pytest -m real_llm` 실행 (Golden Eval)
3. `overall_score >= baseline - 0.05` 확인
4. PR 리뷰 요청
5. 머지 후 baseline 업데이트 여부 판단

**주의:** Windows에서 CP949 인코딩 이슈로 템플릿 저장 시 반드시 UTF-8. `PromptRenderer`는 `encoding="utf-8"`을 명시해둠.

---

## 8. 테스트 전략

```
tests/
├── unit/           외부 의존성 없음, Mock 사용, 빠름
├── integration/    Mock LLM (respx), Mock DB, 중간 속도
├── component/      실제 LLM API 사용 (real_llm 마크)
└── golden/         30개+ 케이스, 회귀 감지
```

### 새 Agent 개발 시 테스트 체크리스트

- [ ] `tests/unit/test_{agent_name}.py` — 핵심 로직 단위 테스트
- [ ] `tests/integration/test_{agent_name}_flow.py` — Mock API로 파이프라인 테스트
- [ ] `tests/golden/datasets/{agent_name}.yaml` — Golden 케이스 최소 10개
- [ ] `pytest tests/unit tests/integration` 100% 통과 확인

### respx로 LLM Mock하는 패턴

```python
import respx, httpx, pytest

@pytest.mark.asyncio
async def test_rag_agent_returns_answer(respx_mock):
    respx_mock.post("http://test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": "PARAM_X는 ...입니다 [doc_0]"}}]
        })
    )
    llm = InternalLLMProvider("http://test/v1", "key", "gpt-oss")
    # ... 검증
```

---

## 9. 사내 시스템 연동 포인트

### 환경변수 설정 (`.env` 파일)

```bash
# LLM
LLM_API_BASE_URL=http://내부LLM서버/v1
LLM_API_KEY=실제키
LLM_MODEL=gpt-oss   # 또는 gemma4

# Oracle App DB (세션/로그 저장)
APP_DB_DSN=사내Oracle호스트:1521/APPDB
APP_DB_USER=voc_app
APP_DB_PASSWORD=실제비밀번호

# Oracle TC DB (read-only, Text-to-SQL 대상)
TC_DB_DSN=tc-oracle-호스트:1521/TCDB
TC_DB_USER=voc_readonly
TC_DB_PASSWORD=실제비밀번호
```

### Oracle 초기 설정

```bash
# DDL 실행 (DBA에게 요청)
sqlplus voc_app@APPDB @db/migrations/001_initial.sql

# whitelist 실제 TC DB 테이블/컬럼으로 수정
vim config/whitelist.yaml
```

### Phase 2 RAG API 연동

```python
# app/infra/rag/confluence_client.py 구현 시
# 사내 Confluence API 스펙 확인 후 채울 것:
BASE_URL = "http://사내confluence/rest/api"
SPACE_KEY = "TC"   # 실제 스페이스 키
```

### Phase 3 Splunk API 연동

```python
# app/infra/splunk/client.py 구현 시
SPLUNK_HOST = "splunk.사내호스트"
SPLUNK_PORT = 8089
SPLUNK_INDEX = "tc_events"   # 실제 인덱스명
```

### 연결 확인 스크립트

사내 첫날 이 3가지를 차례로 실행해서 연결 여부 확인하세요:

```python
# scripts/verify_connections.py (직접 만들거나 REPL에서 실행)

# 1. Oracle App DB
import asyncio, oracledb
async def ping_db():
    pool = oracledb.create_pool_async(
        dsn="실제DSN", user="voc_app", password="비번", min=1, max=2
    )
    async with pool.acquire() as conn:
        cur = conn.cursor()
        await cur.execute("SELECT 1 FROM DUAL")
        print("App DB OK:", await cur.fetchone())
asyncio.run(ping_db())

# 2. Oracle TC DB (read-only) — whitelist 테이블 1개만 읽어보기
# PARAMETER 테이블이 실제로 있는지 확인

# 3. 사내 LLM API
import httpx
r = httpx.post(
    "http://내부LLM/v1/chat/completions",
    headers={"Authorization": "Bearer 키"},
    json={"model": "gpt-oss", "messages": [{"role":"user","content":"ping"}]},
    timeout=10,
)
print("LLM:", r.status_code, r.json()["choices"][0]["message"]["content"][:50])
```

**3가지 중 하나라도 실패하면 Phase 2 개발 시작 전에 인프라 팀에 문의.**

---

## 10. 실행 커맨드 치트시트

### 개발 서버
```bash
# 개발 모드 (코드 변경 자동 반영)
uvicorn app.main:app --reload --port 8000

# 프로덕션 모드
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 테스트
```bash
# 전체 (real_llm 제외)
pytest -m "not real_llm"

# 단위만 (빠름, DB/LLM 불필요)
pytest tests/unit -v

# 통합 (Mock 기반)
pytest tests/integration -v

# 실제 LLM 호출 (환경변수 필요)
pytest -m real_llm tests/component -v

# Golden Eval (baseline 대비 회귀 확인)
pytest -m real_llm tests/golden -v

# 커버리지
pytest --cov=app --cov-report=html
```

### Windows에서 `python` 명령이 Microsoft Store 스텁일 때
```bash
py -3 -m pytest tests/unit -v
py -3 -m uvicorn app.main:app --reload
```

### Oracle 마이그레이션
```bash
# 초기 스키마
sqlplus voc_app/비번@APPDB @db/migrations/001_initial.sql

# Phase 3 추가 (미래)
sqlplus voc_app/비번@APPDB @db/migrations/002_review_flow.sql
```

### Git 워크플로우 (Phase 2 시작)
```bash
git checkout master && git pull
git worktree add .worktrees/phase2-rag -b feature/phase2-rag-agent
cd .worktrees/phase2-rag
pip install -e ".[dev]"
pytest -m "not real_llm"   # 베이스라인 통과 확인
```

---

## 11. API 사용 예시

### `/api/v1/chat` (SSE 스트리밍)

```bash
curl -N -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess_001",
    "user_id": "operator1",
    "message": "A 설비에 PARAM_X 파라미터 있어?"
  }'
```

응답(SSE):
```
event: plan
data: {"agent": "db", "status": "DB 조회 중..."}

event: token
data: {"text": "A "}

event: token
data: {"text": "설비에는 "}

...

event: citation
data: {"citations": [{"id": "row_0", "source_type": "db_row", ...}]}

event: confidence
data: {"score": 0.92, "needs_review": false}

event: done
data: {"message_id": 42}
```

### `/api/v1/feedback`

```bash
curl -X POST http://localhost:8000/api/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "message_id": 42,
    "user_id": "operator1",
    "rating": "U",
    "comment": "잘못된 답변입니다"
  }'
```

`rating`: `"U"` (thumbs up), `"D"` (thumbs down).

### `/health`
```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## 12. Phase 2~4 구현 가이드

### Phase 2: RAG Agent (Confluence 문서 검색)

**목표:** "PARAM_X 기능이 뭐야?" 같은 설명 요청을 Confluence에서 검색해 답변

**새로 만들 파일:**
```
app/infra/rag/
├── __init__.py
├── confluence_client.py    Confluence REST API 어댑터
└── reranker.py             검색 결과 재순위 (TF-IDF 또는 LLM 기반)

app/core/agents/rag/
├── __init__.py
└── agent.py                RAGAgent 구현

config/prompts/
├── rag_query.j2            Confluence 검색 쿼리 생성
└── rag_answer.j2           검색 결과 → 인용 포함 답변

tests/unit/test_rag_*.py
tests/integration/test_rag_flow.py
tests/golden/datasets/rag_phase2.yaml
```

**RAGAgent 구현 패턴:**
```python
@register
class RAGAgent(Agent):
    name = "doc"

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        # 1. 검색 쿼리 생성 (LLM)
        # 2. Confluence 검색
        # 3. Rerank
        # 4. 인용 포함 답변 생성 (LLM)
        # 5. AgentResult 반환
        evidence = [
            Evidence(id=f"doc_{i}", source_type="doc_chunk", content=chunk)
            for i, chunk in enumerate(top_chunks)
        ]
        return AgentResult(
            sub_query_id=sub_query.id,
            success=True,
            evidence=evidence,
            raw_data={"answer": answer, "chunks": top_chunks},
            confidence=confidence,
        )
```

---

### Phase 3: Splunk Agent (오동작 로그 분석)

**목표:** "A 설비 어제 오동작 원인?" → Splunk 로그 분석 → 검토자 승인 후 답변

**새로 만들 파일:**
```
app/infra/splunk/
├── __init__.py
├── client.py               Splunk REST API 어댑터
└── pattern_analyzer.py     로그 패턴 분석

app/core/agents/log/
├── __init__.py
└── agent.py                SplunkAgent (반자동 플로우 포함)

db/migrations/002_review_flow.sql   검토 대기 테이블 추가

config/prompts/
├── splunk_query.j2
└── log_analysis.j2
```

**반자동 플로우 핵심:**
```python
@register
class SplunkAgent(Agent):
    name = "log"

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        # ... 로그 분석 ...
        if confidence < self.review_threshold:
            # 검토자 승인 대기 상태로 저장
            await self.review_repo.create_pending(
                session_id=context.session_id,
                analysis=analysis,
                trace_id=context.trace_id,
            )
            return AgentResult(
                success=True,
                confidence=confidence,
                raw_data={"status": "pending_review", "analysis": analysis},
                ...
            )
        # confidence 높으면 바로 반환
        return AgentResult(success=True, ...)
```

---

### Phase 4: Knowledge Agent + Orchestrator + 품질 자동화

**목표:**
1. KnowledgeAgent — Oracle에 저장된 지식 항목으로 답변
2. Orchestrator — 질문 유형 자동 분류 → 적절한 Agent 선택
3. Synthesizer — 복수 Agent 결과 통합
4. Golden Eval CI — PR마다 회귀 자동 감지

**Orchestrator 핵심:**
```python
# app/core/orchestrator/planner.py
class QueryPlanner:
    async def plan(self, message: str, context: Context) -> list[SubQuery]:
        # LLM으로 질문 분류
        result = await self.llm.complete_json(
            self.renderer.render("planner", message=message)
        )
        # 예: {"agents": ["db"], "sub_queries": [...]}
        # 복합: {"agents": ["db", "doc"], "sub_queries": [...]}
        return [SubQuery(**sq) for sq in result["sub_queries"]]
```

---

## 13. Phase 2 시작 워크플로우 (실전)

사내 첫날 Phase 2를 시작한다면 이 순서대로:

### Step 1. 베이스라인 검증 (30분)
```bash
# 1. 클론 + 설치 (섹션 0 참고)
# 2. .env 작성 (섹션 9 참고)
# 3. Oracle 마이그레이션 실행
# 4. 유닛+통합 테스트 통과 확인
pytest -m "not real_llm"   # 모두 green이어야 함
```

**❗ 여기서 실패하면 Phase 2로 가지 말 것.** Phase 1 환경부터 정상 동작해야 함.

### Step 2. 실제 LLM/DB와 Phase 1 스모크 테스트 (1시간)
```bash
# 서버 띄우고
uvicorn app.main:app --reload

# 다른 터미널에서 DB 연결된 질문 해보기
curl -N -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"s1","user_id":"u1","message":"PARAMETER 테이블에 파라미터가 몇 개야?"}'
```

SSE 이벤트가 순서대로 나오면 LLM + DB 파이프라인 OK.

### Step 3. Golden Dataset baseline 고정 (30분)
```bash
# Phase 1 Golden 평가 실행 (real LLM 호출)
pytest -m real_llm tests/golden -v

# overall_score 확인 후 tests/golden/datasets/db_phase1.yaml 상단 baseline_score 업데이트
# 예: baseline_score: 0.85
# 이 값이 Phase 2+ 회귀 기준이 됨
```

### Step 4. Phase 2 플랜 읽기 + 브랜치 생성
```bash
# 플랜 정독
cat docs/superpowers/plans/2026-04-19-phase2-rag-agent.md

# 워크트리로 격리
git worktree add .worktrees/phase2-rag -b feature/phase2-rag-agent
cd .worktrees/phase2-rag
```

### Step 5. Phase 2 Task 1부터 TDD로 진행
플랜의 Task 1(Confluence API 어댑터)부터 순서대로:
1. 테스트 먼저 작성 (`tests/unit/test_confluence_client.py`)
2. 최소 구현
3. 테스트 통과
4. 커밋
5. 다음 Task

### Step 6. 주기적 Golden 회귀 체크
Phase 2 Task 완료마다:
```bash
pytest -m real_llm tests/golden   # DB Phase 1 점수가 떨어지지 않아야 함
```

---

## 14. 코딩 컨벤션

- **모든 LLM 호출에 trace_id 로깅** — `logger.bind(trace_id=context.trace_id)`
- **외부 의존성은 `app/infra/`에만** — core는 인터페이스만 주입받음
- **`app/infra/db/oracle.py`의 `execute()`** — INSERT/UPDATE용, `fetch_all()`은 SELECT 전용
- **Pydantic v2** — `.dict()` 대신 `.model_dump()` 사용
- **Oracle thin mode** — `oracledb.init_oracle_client()` 절대 호출 금지
- **파일 인코딩 UTF-8** — Windows에서 YAML/Jinja2/Python 저장 시 반드시 UTF-8
- **주석 최소화** — 이유가 명확하지 않은 WHY만 주석으로
- **테스트 먼저** — TDD, 테스트 없는 PR 금지
- **프롬프트 하드코딩 금지** — 반드시 `config/prompts/*.j2`로
- **LLM 호출 temperature 기본 0.0** — 재현성 확보

---

## 15. 흔한 에러와 해결

| 에러 메시지 | 원인 | 해결 |
|-------------|------|------|
| `DPY-4011: the database or network closed the connection` | Oracle 연결 끊김 | pool 재시작, min/max 크기 확인 |
| `DPY-6005: cannot connect to database` | DSN/계정/네트워크 문제 | `tnsping`으로 먼저 확인 |
| `ORA-00942: table or view does not exist` | whitelist에 있지만 DB에 없음 | `config/whitelist.yaml`과 실제 DB 동기화 |
| `ORA-00904: invalid identifier` | 컬럼명 오타 | LLM이 없는 컬럼 생성 → schema linker 프롬프트 개선 |
| `LLMError: LLM이 유효한 JSON을 반환하지 않음` | `complete_json` 실패 | 프롬프트에 "반드시 JSON만" 강조, few-shot 추가 |
| `UnicodeDecodeError: 'cp949'` | Windows 기본 인코딩 | `open(..., encoding='utf-8')` 명시 |
| `ModuleNotFoundError: No module named 'app'` | 설치 안 됨 | `pip install -e ".[dev]"` |
| pytest가 `python`을 못 찾음 (Windows) | Microsoft Store 스텁 | `py -3 -m pytest` 사용 |
| `hatchling.build has no attribute...` | 빌드 설정 | `pyproject.toml`의 `[tool.hatch.build.targets.wheel]` 확인 |
| SSE 이벤트가 안 옴 | proxy 버퍼링 | `uvicorn --no-proxy-headers` 또는 `X-Accel-Buffering: no` 헤더 |
| 프롬프트 변경 후 답변 이상 | Jinja2 문법 에러 조용히 렌더 | `renderer.render()` 결과를 log로 확인 |

### LLM이 계속 JSON 파싱 실패할 때

1. 프롬프트 맨 끝에 예시 JSON 포함 (few-shot)
2. `temperature=0.0` 확인
3. `response_format={"type": "json_object"}` (지원하는 모델이면)
4. 그래도 실패하면 regex로 `\{.*\}` 추출 후 재파싱

### Oracle 연결이 너무 느릴 때

```python
pool = await oracledb.create_pool_async(
    ..., min=2, max=10, increment=1,
    ping_interval=60,      # 60초마다 헬스체크
)
```

### Windows에서 LF/CRLF 경고

```bash
git config --global core.autocrlf input   # 리눅스 스타일로 통일
```

---

## 16. 자주 하는 실수

| 실수 | 올바른 방법 |
|------|-------------|
| `oracledb.init_oracle_client()` 호출 | 삭제 (thin mode 자동) |
| `e.dict()` 사용 | `e.model_dump()` |
| INSERT에 `fetch_all()` 사용 | `pool.execute()` 사용 |
| `RETURNING INTO :var` + `fetch_all` | `execute()` 후 `SELECT MAX(id)` 별도 호출 |
| `assert answer == "정확한 텍스트"` | `assert "키워드" in answer` |
| 프롬프트 수정 후 바로 머지 | Golden Eval 통과 후 머지 |
| `config/whitelist.yaml` DB에 저장 | YAML 파일로만 관리 (보안) |
| core에서 `httpx.get()` 직접 호출 | `app/infra/` 어댑터 통해서 |
| Agent 내부에서 `LLMProvider()` 생성 | `deps.py`에서 주입받기 |
| 프롬프트를 코드에 f-string으로 박기 | `config/prompts/*.j2` 파일로 분리 |
| LLM 호출에 `temperature` 생략 | `temperature=0.0` 명시 (재현성) |
| `trace_id` 로그 생략 | 모든 Agent에서 `logger.bind(trace_id=...)` |
| 새 Agent 만들고 `agents.yaml`에 안 넣음 | `enabled: true`로 등록 필수 |
| 통합 테스트에서 실제 API 호출 | `respx`로 Mock (integration은 격리) |

---

## 부록: 이 문서 업데이트 정책

이 가이드는 프로젝트가 진화하면 **반드시 같이 업데이트**해야 합니다:

- 새 Agent 추가 → 섹션 2, 12에 반영
- 새 의존성 추가 → 섹션 0 Quickstart에 반영
- 흔한 에러 재발 → 섹션 15에 추가
- LLM Provider 인터페이스 변경 → 섹션 6 갱신
- 새 API 엔드포인트 → 섹션 11에 추가

**가이드가 현실과 다르면 AI Agent가 잘못된 코드를 생성합니다.** 이 문서는 코드와 동급의 자산.
