# TC VOC Chatbot Phase 4 — Knowledge Agent + Orchestrator + 품질 자동화

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan.

**Goal:**
1. KnowledgeAgent — Oracle에 저장된 운영 지식으로 답변
2. Orchestrator 완성 — LLM 기반 질문 분류 + 복합 질문 병렬 처리
3. Synthesizer — 복수 Agent 결과 통합
4. 품질 자동화 — Golden Eval CI, 자동 리포트

**Timeline:** 4주 (Week 8~11)

**Prerequisites:**
- Phase 2, 3 완료
- 운영자 지식 입력 UI 또는 DB INSERT 방법 확정

---

## 전체 파일 구조

```
app/core/agents/knowledge/
├── __init__.py
└── agent.py

app/core/synthesizer.py

app/core/orchestrator/
├── planner.py      (Phase 2 기초 → LLM 기반으로 교체)
└── executor.py     (Phase 2 stub → 완성)

db/migrations/003_knowledge.sql

config/prompts/
├── planner.j2
└── synthesizer.j2

tests/integration/
└── test_orchestrator_flow.py
tests/golden/datasets/
└── full_phase4.yaml
```

---

## Week 1 (Week 8): KnowledgeAgent

### Task 1: DDL + Knowledge Repository

**Files:**
- Create: `db/migrations/003_knowledge.sql`
- Create: `app/infra/db/knowledge_repo.py`

- [ ] **Step 1: `db/migrations/003_knowledge.sql`**

```sql
CREATE TABLE knowledge_items (
  item_id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  category       VARCHAR2(50),
  title          CLOB,
  content        CLOB,
  keywords       CLOB,
  enabled        CHAR(1) DEFAULT 'Y',
  created_by     VARCHAR2(50),
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP,
  updated_at     TIMESTAMP
);
CREATE INDEX idx_knowledge_category ON knowledge_items(category, enabled);

-- 시드 데이터 예시
INSERT INTO knowledge_items (category, title, content, keywords)
VALUES ('faq', 'TC 시스템 접근 방법', 'TC 시스템은 사내 VPN 연결 후 접속 가능합니다.', 'TC,접속,VPN');
COMMIT;
```

- [ ] **Step 2: `app/infra/db/knowledge_repo.py` 구현**

```python
from __future__ import annotations
from app.infra.db.oracle import OraclePool
from app.shared.logging import get_logger

logger = get_logger(__name__)


class KnowledgeRepository:
    def __init__(self, app_pool: OraclePool):
        self.pool = app_pool

    async def search(self, keywords: list[str], limit: int = 5) -> list[dict]:
        if not keywords:
            return []
        conditions = " OR ".join([f"UPPER(keywords) LIKE UPPER('%{kw}%')" for kw in keywords[:5]])
        return await self.pool.fetch_all(
            f"SELECT item_id, category, title, content FROM knowledge_items"
            f" WHERE enabled = 'Y' AND ({conditions})"
            f" FETCH FIRST :lim ROWS ONLY",
            {"lim": limit},
        )

    async def get_by_category(self, category: str) -> list[dict]:
        return await self.pool.fetch_all(
            "SELECT item_id, title, content FROM knowledge_items"
            " WHERE category = :cat AND enabled = 'Y' ORDER BY created_at DESC",
            {"cat": category},
        )
```

- [ ] **Step 3: 단위 테스트**

```python
# tests/unit/test_knowledge_repo.py
import pytest
from unittest.mock import AsyncMock, patch
from app.infra.db.knowledge_repo import KnowledgeRepository

@pytest.mark.asyncio
async def test_search_returns_items():
    mock_pool = AsyncMock()
    mock_pool.fetch_all = AsyncMock(return_value=[
        {"item_id": 1, "category": "faq", "title": "TC 접속 방법", "content": "VPN 연결 필요"}
    ])
    repo = KnowledgeRepository(mock_pool)
    results = await repo.search(["TC", "접속"])
    assert len(results) > 0
```

- [ ] **Step 4: Commit**
```bash
git add db/migrations/003_knowledge.sql app/infra/db/knowledge_repo.py tests/unit/test_knowledge_repo.py
git commit -m "feat: knowledge DDL + KnowledgeRepository"
```

---

### Task 2: KnowledgeAgent 구현

**Files:**
- Create: `app/core/agents/knowledge/__init__.py`
- Create: `app/core/agents/knowledge/agent.py`

- [ ] **Step 1: KnowledgeAgent 구현**

```python
# app/core/agents/knowledge/agent.py
from __future__ import annotations
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.infra.db.knowledge_repo import KnowledgeRepository
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, AgentResult, Evidence, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


@register
class KnowledgeAgent(Agent):
    name = "knowledge"

    def __init__(self, llm: LLMProvider, renderer: PromptRenderer, repo: KnowledgeRepository):
        self.llm = llm
        self.renderer = renderer
        self.repo = repo

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        question = sub_query.query
        log = logger.bind(trace_id=context.trace_id, agent="knowledge")

        # 키워드 추출 (LLM 또는 간단한 토크나이저)
        keywords = [w for w in question.split() if len(w) >= 2][:5]
        items = await self.repo.search(keywords)

        if not items:
            return AgentResult(
                sub_query_id=sub_query.id, success=True,
                evidence=[], raw_data={"answer": "관련 지식 항목이 없습니다"},
                confidence=0.0,
            )

        # RAG 방식과 동일한 answer 생성 패턴
        prompt = self.renderer.render(
            "rag_answer",  # 재사용
            question=question,
            docs=[{"title": i["title"], "content": i["content"]} for i in items],
        )
        result = await self.llm.complete_json(prompt)
        answer = result.get("answer", "")
        confidence = result.get("confidence", 0.0)

        evidences = [
            Evidence(
                id=f"kb_{i+1}", source_type="knowledge_entry",
                content=item["content"][:500],
                metadata={"item_id": item["item_id"], "category": item["category"]},
            )
            for i, item in enumerate(items)
        ]

        log.info("knowledge_complete", items=len(items), confidence=confidence)
        return AgentResult(
            sub_query_id=sub_query.id, success=True,
            evidence=evidences,
            raw_data={"answer": answer, "items": items},
            confidence=confidence,
        )
```

- [ ] **Step 2: Commit**
```bash
git add app/core/agents/knowledge/
git commit -m "feat: KnowledgeAgent"
```

---

## Week 2 (Week 9): Orchestrator 완성

### Task 3: Planner (LLM 기반) + Executor 완성

**Files:**
- Create: `config/prompts/planner.j2`
- Modify: `app/core/orchestrator/planner.py`
- Modify: `app/core/orchestrator/executor.py`

- [ ] **Step 1: `config/prompts/planner.j2`**

```
사용자 질문을 분석하여 어떤 Agent가 필요한지 결정하세요.

[사용 가능한 Agent]
- db: Oracle DB 조회 (설비 파라미터, 모델 정보, DCOL 항목 조회/비교)
- doc: Confluence 문서 검색 (기능 설명, 사용법)
- log: Splunk 로그 분석 (오동작 원인, 에러 분석)
- knowledge: 사내 지식베이스 (FAQ, 운영 절차)

[질문]
{{ question }}

단일 Agent로 충분하면 1개, 복합 질문이면 2개까지 선택 가능.

JSON으로 출력:
{
  "sub_queries": [
    {"agent": "db", "query": "구체화된 질문"},
    {"agent": "doc", "query": "구체화된 질문"}
  ]
}
```

- [ ] **Step 2: `app/core/orchestrator/planner.py` 교체 (LLM 기반)**

```python
from __future__ import annotations
import uuid
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery
from app.shared.logging import get_logger

logger = get_logger(__name__)


class QueryPlanner:
    def __init__(self, llm: LLMProvider, renderer: PromptRenderer):
        self.llm = llm
        self.renderer = renderer

    async def plan(self, message: str) -> list[SubQuery]:
        result = await self.llm.complete_json(
            self.renderer.render("planner", question=message)
        )
        sub_queries = []
        for sq in result.get("sub_queries", []):
            sub_queries.append(SubQuery(
                id=str(uuid.uuid4()),
                agent=sq.get("agent", "db"),
                query=sq.get("query", message),
            ))
        logger.info("query_planned", agents=[sq.agent for sq in sub_queries])
        return sub_queries or [SubQuery(id=str(uuid.uuid4()), agent="db", query=message)]
```

- [ ] **Step 3: `app/core/orchestrator/executor.py` 완성**

```python
from __future__ import annotations
import asyncio
from app.shared.schemas import SubQuery, AgentResult, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


class QueryExecutor:
    def __init__(self, agents: dict):  # {"db": DBAgent, "doc": RAGAgent, ...}
        self.agents = agents

    async def execute(self, sub_queries: list[SubQuery], context: Context) -> list[AgentResult]:
        tasks = [
            self._run(sq, context)
            for sq in sub_queries
            if sq.agent in self.agents
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def _run(self, sq: SubQuery, context: Context) -> AgentResult:
        agent = self.agents[sq.agent]
        logger.info("agent_start", agent=sq.agent, trace_id=context.trace_id)
        return await agent.run(sq, context)
```

- [ ] **Step 4: 통합 테스트**

```python
# tests/integration/test_orchestrator_flow.py
import pytest
import respx
import httpx
from unittest.mock import AsyncMock
from app.core.orchestrator.planner import QueryPlanner
from app.core.orchestrator.executor import QueryExecutor
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import Context, AgentResult
import uuid


@pytest.mark.asyncio
async def test_planner_routes_to_db(tmp_path):
    llm = InternalLLMProvider(base_url="http://mock-llm", api_key="k", model="test")
    (tmp_path / "planner.j2").write_text("q: {{ question }}", encoding="utf-8")
    renderer = PromptRenderer(prompt_dir=str(tmp_path))
    planner = QueryPlanner(llm=llm, renderer=renderer)

    with respx.mock:
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"sub_queries":[{"agent":"db","query":"A 설비 파라미터"}]}'}}]})
        )
        sqs = await planner.plan("A 설비 파라미터 있어?")

    assert len(sqs) == 1
    assert sqs[0].agent == "db"


@pytest.mark.asyncio
async def test_executor_runs_multiple_agents():
    mock_db = AsyncMock()
    mock_doc = AsyncMock()
    mock_db.run = AsyncMock(return_value=AgentResult(sub_query_id="1", success=True, evidence=[], raw_data={}, confidence=0.9))
    mock_doc.run = AsyncMock(return_value=AgentResult(sub_query_id="2", success=True, evidence=[], raw_data={}, confidence=0.8))

    executor = QueryExecutor(agents={"db": mock_db, "doc": mock_doc})
    from app.shared.schemas import SubQuery
    results = await executor.execute(
        [SubQuery(id="1", agent="db", query="파라미터?"), SubQuery(id="2", agent="doc", query="설명?")],
        Context(session_id="s1", trace_id=str(uuid.uuid4())),
    )
    assert len(results) == 2
```

- [ ] **Step 5: Commit**
```bash
git add app/core/orchestrator/ config/prompts/planner.j2 tests/integration/test_orchestrator_flow.py
git commit -m "feat: LLM-based query planner + parallel executor"
```

---

## Week 3 (Week 10): Synthesizer + chat.py 연결

### Task 4: Synthesizer + chat.py 업데이트

**Files:**
- Create: `app/core/synthesizer.py`
- Modify: `app/api/v1/chat.py`
- Modify: `app/api/deps.py`

- [ ] **Step 1: `config/prompts/synthesizer_multi.j2` 작성**

```
여러 소스에서 얻은 정보를 통합하여 답변하세요.

[질문]
{{ question }}

{% for result in results %}
[{{ result.agent }} 결과]
{{ result.answer }}
{% endfor %}

통합 답변 JSON:
{"answer": "통합 답변", "confidence": 0.0, "sources": ["db", "doc"]}
```

- [ ] **Step 2: `app/core/synthesizer.py` 구현**

```python
from __future__ import annotations
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import AgentResult
from app.shared.logging import get_logger

logger = get_logger(__name__)


class Synthesizer:
    def __init__(self, llm: LLMProvider, renderer: PromptRenderer):
        self.llm = llm
        self.renderer = renderer

    async def synthesize(self, question: str, results: list[AgentResult]) -> dict:
        if len(results) == 1:
            # 단일 결과는 그대로 반환
            raw = results[0].raw_data or {}
            return {
                "answer": raw.get("answer", ""),
                "confidence": results[0].confidence,
                "sources": [results[0].sub_query_id],
            }

        # 복수 결과 통합
        result_summaries = [
            {
                "agent": r.sub_query_id,
                "answer": (r.raw_data or {}).get("answer", ""),
            }
            for r in results if r.success
        ]
        return await self.llm.complete_json(
            self.renderer.render("synthesizer_multi", question=question, results=result_summaries)
        )
```

- [ ] **Step 3: `app/api/v1/chat.py` Orchestrator 연결**

`chat.py`의 `_stream` 함수를 아래 구조로 교체:

```python
async def _stream(req: ChatRequest, planner: QueryPlanner, executor: QueryExecutor,
                  synthesizer: Synthesizer, session_repo: SessionRepository):
    trace_id = get_trace_id()
    ctx = Context(session_id=req.session_id, trace_id=trace_id)

    await session_repo.get_or_create(req.session_id, req.user_id)
    await session_repo.save_message(req.session_id, "user", req.message, [], 1.0, trace_id)

    # 1. 계획
    sub_queries = await planner.plan(req.message)
    yield _sse("plan", {"agents": [sq.agent for sq in sub_queries], "status": "분석 중..."})

    # 2. 실행
    results = await executor.execute(sub_queries, ctx)

    # 3. 통합
    synthesis = await synthesizer.synthesize(req.message, results)
    answer = synthesis.get("answer", "")
    confidence = synthesis.get("confidence", 0.0)

    all_evidence = [e for r in results for e in r.evidence]
    citations = [e.model_dump() for e in all_evidence]

    for chunk in answer.split(" "):
        yield _sse("token", {"text": chunk + " "})

    yield _sse("citation", {"citations": citations})
    yield _sse("confidence", {"score": confidence, "needs_review": confidence < 0.7})

    msg_id = await session_repo.save_message(
        req.session_id, "assistant", answer, citations, confidence, trace_id
    )
    yield _sse("done", {"message_id": msg_id})
```

- [ ] **Step 4: Commit**
```bash
git add app/core/synthesizer.py app/api/v1/chat.py config/prompts/synthesizer_multi.j2
git commit -m "feat: Synthesizer + Orchestrator wired to chat endpoint"
```

---

## Week 4 (Week 11): 품질 자동화 + 최종 통합

### Task 5: Golden Dataset 확장 + 전체 테스트

- [ ] **Step 1: `tests/golden/datasets/full_phase4.yaml` 작성 (20개+)**

```yaml
baseline_score: null
examples:
  - id: full_001
    difficulty: easy
    question: "A 설비에 PARAM_X 있나?"
    expected:
      agent: db
      citation_required: true

  - id: full_002
    difficulty: easy
    question: "PARAM_X 기능 설명해줘"
    expected:
      agent: doc
      citation_required: true

  - id: full_003
    difficulty: medium
    question: "A 설비 오동작 원인이 뭐야?"
    expected:
      agent: log
      citation_required: true

  - id: full_004
    difficulty: hard
    question: "A 설비 PARAM_X 있는데 그게 어떤 기능이야?"
    expected:
      agents: [db, doc]   # 복합 질문
      citation_required: true

  # ... 16개 더
```

- [ ] **Step 2: `tests/golden/runner.py` 확장 — agent 분류 정확도 추가**

- [ ] **Step 3: 전체 테스트 통과**
```bash
pytest tests/unit tests/integration -v --tb=short
```
Expected: 전체 통과

- [ ] **Step 4: 최종 Commit**
```bash
git add tests/golden/datasets/full_phase4.yaml
git commit -m "feat: Phase 4 complete — Knowledge Agent + Orchestrator + Synthesizer + 품질 자동화"
git tag phase4-complete
```

---

## Self-Review

| 스펙 요구사항 | 구현 Task |
|------------|---------|
| KnowledgeAgent | Task 1, 2 |
| Orchestrator Planner (LLM 기반) | Task 3 |
| Executor (병렬 실행) | Task 3 |
| Synthesizer (복합 결과 통합) | Task 4 |
| chat.py Orchestrator 연결 | Task 4 |
| Golden Dataset (복합 질문 포함) | Task 5 |

---

## 전체 개발 타임라인

| 주차 | Phase | 주요 내용 |
|------|-------|-----------|
| Week 1 | Phase 1 ✅ | DB Agent 전체 구현 |
| Week 2~4 | Phase 2 | RAG Agent (Confluence) |
| Week 5~7 | Phase 3 | Splunk Agent + 검토자 승인 |
| Week 8~11 | Phase 4 | Knowledge + Orchestrator + 품질 자동화 |
| **총 11주** | | **~2.5개월** |
