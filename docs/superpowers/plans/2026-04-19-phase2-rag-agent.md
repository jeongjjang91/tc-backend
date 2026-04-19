# TC VOC Chatbot Phase 2 — RAG Agent (Confluence) 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan.

**Goal:** 사용자의 기능 설명 요청(VOC 유형 4)에 Confluence 문서를 검색해 인용 포함 답변.

**Timeline:** 3주 (Week 2~4, Phase 1 완료 후)

**Prerequisites:**
- Phase 1 완료 (`feature/phase1-db-agent` merge)
- 사내 Confluence REST API 스펙 확인
- `LLM_API_BASE_URL` 실제 사내 주소 설정

---

## 전체 파일 구조

```
app/infra/rag/
├── __init__.py
├── confluence_client.py
└── reranker.py

app/core/agents/rag/
├── __init__.py
└── agent.py

config/prompts/
├── rag_query.j2
└── rag_answer.j2

tests/unit/
├── test_confluence_client.py
└── test_reranker.py
tests/integration/
└── test_rag_flow.py
tests/golden/datasets/
└── rag_phase2.yaml
```

---

## Week 1: Confluence API 어댑터 + Reranker

### Task 1: Confluence API 어댑터

**Files:**
- Create: `app/infra/rag/__init__.py`
- Create: `app/infra/rag/confluence_client.py`
- Create: `tests/unit/test_confluence_client.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_confluence_client.py
import pytest
import respx
import httpx
from app.infra.rag.confluence_client import ConfluenceClient

@pytest.mark.asyncio
async def test_search_returns_chunks():
    client = ConfluenceClient(base_url="http://mock-confluence", token="tok", space_key="TC")
    with respx.mock:
        respx.get("http://mock-confluence/rest/api/content/search").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": "1", "title": "PARAM_X 설명", "body": {"storage": {"value": "<p>PARAM_X는 온도 파라미터입니다.</p>"}}}
                ]
            })
        )
        chunks = await client.search("PARAM_X 기능")
    assert len(chunks) > 0
    assert "PARAM_X" in chunks[0]["content"]

@pytest.mark.asyncio
async def test_search_empty_returns_empty():
    client = ConfluenceClient(base_url="http://mock-confluence", token="tok", space_key="TC")
    with respx.mock:
        respx.get("http://mock-confluence/rest/api/content/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        chunks = await client.search("존재하지않는기능")
    assert chunks == []
```

- [ ] **Step 2: 실패 확인**
```bash
pytest tests/unit/test_confluence_client.py -v
```

- [ ] **Step 3: `app/infra/rag/confluence_client.py` 구현**

```python
from __future__ import annotations
import re
import httpx
from app.shared.logging import get_logger

logger = get_logger(__name__)


class ConfluenceClient:
    def __init__(self, base_url: str, token: str, space_key: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.space_key = space_key
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
        )

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        try:
            resp = await self._client.get(
                f"{self.base_url}/rest/api/content/search",
                params={
                    "cql": f'space="{self.space_key}" AND text~"{query}"',
                    "limit": limit,
                    "expand": "body.storage",
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "content": self._strip_html(r["body"]["storage"]["value"]),
                    "url": f"{self.base_url}/pages/{r['id']}",
                }
                for r in results
            ]
        except httpx.HTTPError as e:
            logger.error("confluence_search_failed", query=query, error=str(e))
            return []

    @staticmethod
    def _strip_html(html: str) -> str:
        return re.sub(r"<[^>]+>", "", html).strip()
```

- [ ] **Step 4: 테스트 통과**
```bash
pytest tests/unit/test_confluence_client.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**
```bash
git add app/infra/rag/ tests/unit/test_confluence_client.py
git commit -m "feat: Confluence API client"
```

---

### Task 2: Reranker

**Files:**
- Create: `app/infra/rag/reranker.py`
- Create: `tests/unit/test_reranker.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_reranker.py
from app.infra.rag.reranker import TFIDFReranker

def test_rerank_returns_top_k():
    reranker = TFIDFReranker()
    chunks = [
        {"id": "1", "title": "PARAM_X", "content": "PARAM_X는 온도 관련 파라미터입니다"},
        {"id": "2", "title": "기타", "content": "관련없는 내용"},
        {"id": "3", "title": "PARAM_X 상세", "content": "PARAM_X의 상세 설명"},
    ]
    results = reranker.rerank("PARAM_X 기능 설명", chunks, top_k=2)
    assert len(results) == 2
    assert results[0]["id"] in ["1", "3"]

def test_rerank_empty_returns_empty():
    reranker = TFIDFReranker()
    assert reranker.rerank("query", [], top_k=3) == []
```

- [ ] **Step 2: `app/infra/rag/reranker.py` 구현**

```python
from __future__ import annotations
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class TFIDFReranker:
    def rerank(self, query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
        if not chunks:
            return []
        docs = [f"{c['title']} {c['content']}" for c in chunks]
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        matrix = vectorizer.fit_transform(docs)
        q_vec = vectorizer.transform([query])
        scores = cosine_similarity(q_vec, matrix).flatten()
        top_idx = np.argsort(scores)[::-1][:top_k]
        return [chunks[i] for i in top_idx]
```

- [ ] **Step 3: 테스트 통과**
```bash
pytest tests/unit/test_reranker.py -v
```
Expected: 2 passed

- [ ] **Step 4: Commit**
```bash
git add app/infra/rag/reranker.py tests/unit/test_reranker.py
git commit -m "feat: TF-IDF reranker for RAG chunks"
```

---

## Week 2: RAGAgent 구현

### Task 3: 프롬프트 템플릿 + RAGAgent

**Files:**
- Create: `config/prompts/rag_query.j2`
- Create: `config/prompts/rag_answer.j2`
- Create: `app/core/agents/rag/__init__.py`
- Create: `app/core/agents/rag/agent.py`
- Create: `tests/integration/test_rag_flow.py`

- [ ] **Step 1: 프롬프트 템플릿 작성**

`config/prompts/rag_query.j2`:
```
사용자 질문에서 Confluence 검색에 적합한 키워드를 추출하세요.

[질문]
{{ question }}

JSON으로 출력:
{"keywords": ["키워드1", "키워드2"], "query": "검색 쿼리 문자열"}
```

`config/prompts/rag_answer.j2`:
```
아래 문서를 바탕으로 질문에 답하세요.

[규칙]
1. 모든 주장에 [doc_N] 형식의 인용 포함
2. 문서에 없는 내용 추가 금지
3. 정보가 없으면 "문서에서 확인되지 않습니다" 답변

[질문]
{{ question }}

[문서]
{% for doc in docs %}
[doc_{{ loop.index }}] 제목: {{ doc.title }}
{{ doc.content }}
{% endfor %}

JSON으로 출력:
{"answer": "인용 포함 답변", "confidence": 0.0, "needs_human_review": false}
```

- [ ] **Step 2: RAGAgent 구현**

```python
# app/core/agents/rag/agent.py
from __future__ import annotations
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.infra.rag.confluence_client import ConfluenceClient
from app.infra.rag.reranker import TFIDFReranker
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, AgentResult, Evidence, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


@register
class RAGAgent(Agent):
    name = "doc"

    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        confluence: ConfluenceClient,
        reranker: TFIDFReranker,
        top_k: int = 5,
        confidence_threshold: float = 0.7,
    ):
        self.llm = llm
        self.renderer = renderer
        self.confluence = confluence
        self.reranker = reranker
        self.top_k = top_k
        self.confidence_threshold = confidence_threshold

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        question = sub_query.query
        log = logger.bind(trace_id=context.trace_id, agent="doc")

        # 1. 검색 쿼리 생성
        log.info("rag_query_start")
        query_result = await self.llm.complete_json(
            self.renderer.render("rag_query", question=question)
        )
        search_query = query_result.get("query", question)

        # 2. Confluence 검색
        chunks = await self.confluence.search(search_query, limit=10)
        if not chunks:
            return AgentResult(
                sub_query_id=sub_query.id,
                success=True,
                evidence=[],
                raw_data={"answer": "문서에서 확인되지 않습니다", "chunks": []},
                confidence=0.0,
            )

        # 3. Rerank
        top_chunks = self.reranker.rerank(question, chunks, top_k=self.top_k)

        # 4. 답변 생성
        answer_result = await self.llm.complete_json(
            self.renderer.render("rag_answer", question=question, docs=top_chunks)
        )
        answer = answer_result.get("answer", "")
        confidence = answer_result.get("confidence", 0.0)

        evidences = [
            Evidence(
                id=f"doc_{i+1}",
                source_type="doc_chunk",
                content=chunk["content"][:500],
                metadata={"title": chunk["title"], "url": chunk.get("url", ""), "doc_index": i},
            )
            for i, chunk in enumerate(top_chunks)
        ]

        log.info("rag_complete", chunks=len(top_chunks), confidence=confidence)
        return AgentResult(
            sub_query_id=sub_query.id,
            success=True,
            evidence=evidences,
            raw_data={"answer": answer, "chunks": top_chunks},
            confidence=confidence,
        )
```

- [ ] **Step 3: 통합 테스트**

```python
# tests/integration/test_rag_flow.py
import pytest
import respx
import httpx
from unittest.mock import AsyncMock
from app.core.agents.rag.agent import RAGAgent
from app.infra.rag.confluence_client import ConfluenceClient
from app.infra.rag.reranker import TFIDFReranker
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, Context
import uuid


@pytest.fixture
def llm():
    return InternalLLMProvider(base_url="http://mock-llm", api_key="k", model="test")

@pytest.fixture
def renderer(tmp_path):
    (tmp_path / "rag_query.j2").write_text('q: {{ question }}', encoding="utf-8")
    (tmp_path / "rag_answer.j2").write_text('q: {{ question }}\ndocs: {{ docs }}', encoding="utf-8")
    return PromptRenderer(prompt_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_rag_agent_returns_answer(llm, renderer):
    confluence = ConfluenceClient(base_url="http://mock-conf", token="t", space_key="TC")
    reranker = TFIDFReranker()
    agent = RAGAgent(llm=llm, renderer=renderer, confluence=confluence, reranker=reranker)

    with respx.mock:
        respx.get("http://mock-conf/rest/api/content/search").mock(
            return_value=httpx.Response(200, json={"results": [
                {"id": "1", "title": "PARAM_X", "body": {"storage": {"value": "PARAM_X는 온도 파라미터"}}}
            ]})
        )
        respx.post("http://mock-llm/chat/completions").mock(side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"keywords":["PARAM_X"],"query":"PARAM_X 기능"}'}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"PARAM_X는 온도 파라미터입니다[doc_1]","confidence":0.85,"needs_human_review":false}'}}]}),
        ])
        result = await agent.run(
            SubQuery(id="t1", agent="doc", query="PARAM_X 기능이 뭐야?"),
            Context(session_id="s1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert len(result.evidence) > 0
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_rag_agent_no_results_returns_gracefully(llm, renderer):
    confluence = ConfluenceClient(base_url="http://mock-conf", token="t", space_key="TC")
    reranker = TFIDFReranker()
    agent = RAGAgent(llm=llm, renderer=renderer, confluence=confluence, reranker=reranker)

    with respx.mock:
        respx.get("http://mock-conf/rest/api/content/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        respx.post("http://mock-llm/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"keywords":[],"query":"없는기능"}'}}]})
        )
        result = await agent.run(
            SubQuery(id="t2", agent="doc", query="없는기능 설명"),
            Context(session_id="s1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert result.confidence == 0.0
```

- [ ] **Step 4: 테스트 통과**
```bash
pytest tests/unit tests/integration/test_rag_flow.py -v
```
Expected: 모두 통과

- [ ] **Step 5: Commit**
```bash
git add app/core/agents/rag/ config/prompts/rag_*.j2 tests/integration/test_rag_flow.py
git commit -m "feat: RAG Agent with Confluence search and TF-IDF reranker"
```

---

## Week 3: Orchestrator 기초 + deps.py 연동

### Task 4: Orchestrator Planner (기초)

**Files:**
- Create: `app/core/orchestrator/planner.py`
- Create: `app/core/orchestrator/executor.py`
- Modify: `app/api/deps.py`
- Modify: `app/api/v1/chat.py`

- [ ] **Step 1: `app/core/orchestrator/planner.py` 구현**

```python
# app/core/orchestrator/planner.py
from __future__ import annotations
import re
from app.shared.schemas import SubQuery
from app.shared.logging import get_logger

logger = get_logger(__name__)

# 규칙 기반 분류 (Phase 4에서 LLM 기반으로 업그레이드)
_DOC_PATTERNS = [
    r"설명", r"기능이 뭐", r"어떻게 동작", r"뭐야\?", r"무엇", r"what is",
]
_LOG_PATTERNS = [
    r"오동작", r"에러", r"오류", r"왜 안", r"문제", r"장애",
]


def classify_question(message: str) -> str:
    msg = message.lower()
    for pat in _LOG_PATTERNS:
        if re.search(pat, msg):
            return "log"
    for pat in _DOC_PATTERNS:
        if re.search(pat, msg):
            return "doc"
    return "db"  # 기본값


class QueryPlanner:
    def plan(self, message: str, session_id: str) -> list[SubQuery]:
        import uuid
        agent_name = classify_question(message)
        logger.info("query_classified", agent=agent_name, message=message[:50])
        return [SubQuery(id=str(uuid.uuid4()), agent=agent_name, query=message)]
```

- [ ] **Step 2: `app/core/orchestrator/executor.py` 구현**

```python
# app/core/orchestrator/executor.py
from __future__ import annotations
import asyncio
from app.core.agents.registry import AGENT_REGISTRY
from app.shared.schemas import SubQuery, AgentResult, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


class QueryExecutor:
    async def execute(self, sub_queries: list[SubQuery], context: Context) -> list[AgentResult]:
        tasks = []
        for sq in sub_queries:
            agent_cls = AGENT_REGISTRY.get(sq.agent)
            if not agent_cls:
                logger.warning("agent_not_found", agent=sq.agent)
                continue
            # deps.py에서 주입된 인스턴스 사용 필요 — Phase 4에서 DI 개선
            tasks.append(self._run_agent(sq, context))
        return await asyncio.gather(*tasks)

    async def _run_agent(self, sq: SubQuery, context: Context) -> AgentResult:
        raise NotImplementedError("Phase 4에서 DI와 함께 완성")
```

- [ ] **Step 3: deps.py에 RAGAgent 추가**

`app/api/deps.py`의 `init_dependencies()`에 추가:
```python
from app.infra.rag.confluence_client import ConfluenceClient
from app.infra.rag.reranker import TFIDFReranker
from app.core.agents.rag.agent import RAGAgent

# init_dependencies() 안에:
confluence = ConfluenceClient(
    base_url=s.confluence_base_url,
    token=s.confluence_token,
    space_key=s.confluence_space_key,
)
_rag_agent = RAGAgent(
    llm=llm, renderer=renderer,
    confluence=confluence, reranker=TFIDFReranker(),
)
```

`app/config.py`에 추가:
```python
confluence_base_url: str = "http://localhost/confluence"
confluence_token: str = ""
confluence_space_key: str = "TC"
```

`.env.example`에 추가:
```
CONFLUENCE_BASE_URL=http://사내confluence
CONFLUENCE_TOKEN=your-token
CONFLUENCE_SPACE_KEY=TC
```

- [ ] **Step 4: Golden Dataset 작성**

`tests/golden/datasets/rag_phase2.yaml` — 최소 10개 케이스:
```yaml
baseline_score: null
examples:
  - id: rag_001
    difficulty: easy
    question: "PARAM_TEMP 기능이 뭐야?"
    expected:
      answer_must_contain: ["PARAM_TEMP"]
      citation_required: true

  - id: rag_002
    difficulty: easy
    question: "DCOL 기능 설명해줘"
    expected:
      citation_required: true

  # ... 8개 더 추가
```

- [ ] **Step 5: 전체 테스트 통과**
```bash
pytest tests/unit tests/integration -v
```

- [ ] **Step 6: Commit**
```bash
git add app/core/orchestrator/ app/api/deps.py app/config.py tests/golden/datasets/rag_phase2.yaml
git commit -m "feat: Phase 2 complete — RAG Agent + Orchestrator 기초"
git tag phase2-complete
```

---

## Self-Review

| 스펙 요구사항 | 구현 Task |
|------------|---------|
| Confluence 검색 어댑터 | Task 1 |
| Reranker | Task 2 |
| RAGAgent (인용 강제) | Task 3 |
| 프롬프트 템플릿 | Task 3 |
| Orchestrator 기초 (규칙 기반) | Task 4 |
| Golden Dataset 10개+ | Task 4 |
