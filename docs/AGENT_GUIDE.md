# TC VOC Chatbot — AI Agent 개발 가이드

> 이 문서는 사내 AI Agent(Claude Code, OpenCode 등)가 이 프로젝트를 이해하고
> Phase 2~4를 이어서 개발할 수 있도록 작성된 상세 온보딩 가이드입니다.

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

## 6. 테스트 전략

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

---

## 7. 사내 시스템 연동 포인트

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

---

## 8. Phase 2~4 구현 가이드

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

## 9. 코딩 컨벤션

- **모든 LLM 호출에 trace_id 로깅** — `logger.bind(trace_id=context.trace_id)`
- **외부 의존성은 `app/infra/`에만** — core는 인터페이스만 주입받음
- **`app/infra/db/oracle.py`의 `execute()`** — INSERT/UPDATE용, `fetch_all()`은 SELECT 전용
- **Pydantic v2** — `.dict()` 대신 `.model_dump()` 사용
- **Oracle thin mode** — `oracledb.init_oracle_client()` 절대 호출 금지
- **주석 최소화** — 이유가 명확하지 않은 WHY만 주석으로
- **테스트 먼저** — TDD, 테스트 없는 PR 금지

---

## 10. 자주 하는 실수

| 실수 | 올바른 방법 |
|------|-------------|
| `oracledb.init_oracle_client()` 호출 | 삭제 (thin mode 자동) |
| `e.dict()` 사용 | `e.model_dump()` |
| INSERT에 `fetch_all()` 사용 | `pool.execute()` 사용 |
| `assert answer == "정확한 텍스트"` | `assert "키워드" in answer` |
| 프롬프트 수정 후 바로 머지 | Golden Eval 통과 후 머지 |
| `config/whitelist.yaml` DB에 저장 | YAML 파일로만 관리 (보안) |
