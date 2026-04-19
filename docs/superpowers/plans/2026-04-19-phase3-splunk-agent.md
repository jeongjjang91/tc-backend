# TC VOC Chatbot Phase 3 — Splunk Agent (오동작 분석) 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan.

**Goal:** 설비 오동작 원인 질문(VOC 유형 3)을 Splunk 로그 분석으로 처리. 신뢰도 낮으면 검토자 승인 후 답변.

**Timeline:** 3주 (Week 5~7)

**Prerequisites:**
- Phase 2 완료
- 사내 Splunk REST API 접근 권한 및 스펙 확인
- Splunk 인덱스명, 필드명 확인

---

## 전체 파일 구조

```
app/infra/splunk/
├── __init__.py
├── client.py
└── pattern_analyzer.py

app/core/agents/log/
├── __init__.py
└── agent.py

db/migrations/002_review_flow.sql

config/prompts/
├── splunk_query.j2
└── log_analysis.j2

tests/unit/
├── test_splunk_client.py
└── test_pattern_analyzer.py
tests/integration/
└── test_splunk_flow.py
tests/golden/datasets/
└── splunk_phase3.yaml
```

---

## Week 1 (Week 5): Splunk 어댑터 + 패턴 분석

### Task 1: Splunk API 어댑터

**Files:**
- Create: `app/infra/splunk/__init__.py`
- Create: `app/infra/splunk/client.py`
- Create: `tests/unit/test_splunk_client.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_splunk_client.py
import pytest
import respx
import httpx
from app.infra.splunk.client import SplunkClient

@pytest.mark.asyncio
async def test_search_returns_events():
    client = SplunkClient(host="mock-splunk", port=8089, token="tok", index="tc_events")
    with respx.mock:
        respx.post("https://mock-splunk:8089/services/search/jobs").mock(
            return_value=httpx.Response(201, json={"sid": "job123"})
        )
        respx.get("https://mock-splunk:8089/services/search/jobs/job123/results").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"_time": "2026-04-18T10:00:00", "host": "EQP_A_001", "message": "ERROR: sensor timeout", "level": "ERROR"}
                ]
            })
        )
        events = await client.search(
            query="index=tc_events host=EQP_A_001 level=ERROR",
            earliest="-24h",
        )
    assert len(events) > 0
    assert events[0]["host"] == "EQP_A_001"

@pytest.mark.asyncio
async def test_search_timeout_raises():
    import asyncio
    client = SplunkClient(host="mock-splunk", port=8089, token="tok", index="tc_events", timeout_sec=0.001)
    with respx.mock:
        async def slow(*args, **kwargs):
            await asyncio.sleep(10)
            return httpx.Response(200)
        respx.post("https://mock-splunk:8089/services/search/jobs").mock(side_effect=slow)
        with pytest.raises(Exception):
            await client.search("index=tc_events")
```

- [ ] **Step 2: `app/infra/splunk/client.py` 구현**

```python
from __future__ import annotations
import asyncio
import httpx
from app.shared.exceptions import DBExecutionError
from app.shared.logging import get_logger

logger = get_logger(__name__)


class SplunkClient:
    def __init__(self, host: str, port: int, token: str, index: str, timeout_sec: float = 30.0):
        self.base_url = f"https://{host}:{port}"
        self.index = index
        self.timeout_sec = timeout_sec
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            verify=False,  # 사내 인증서 이슈 대응
            timeout=timeout_sec,
        )

    async def search(self, query: str, earliest: str = "-24h", latest: str = "now") -> list[dict]:
        try:
            async with asyncio.timeout(self.timeout_sec):
                # 1. 검색 Job 생성
                resp = await self._client.post(
                    f"{self.base_url}/services/search/jobs",
                    data={
                        "search": f"search {query}",
                        "earliest_time": earliest,
                        "latest_time": latest,
                        "output_mode": "json",
                    },
                )
                resp.raise_for_status()
                sid = resp.json()["sid"]

                # 2. 결과 조회 (폴링 방식)
                for _ in range(30):
                    result_resp = await self._client.get(
                        f"{self.base_url}/services/search/jobs/{sid}/results",
                        params={"output_mode": "json", "count": 1000},
                    )
                    if result_resp.status_code == 200:
                        return result_resp.json().get("results", [])
                    await asyncio.sleep(1)
                raise DBExecutionError("Splunk search timed out waiting for results")

        except asyncio.TimeoutError:
            raise DBExecutionError(f"Splunk query timed out after {self.timeout_sec}s")
        except httpx.HTTPError as e:
            raise DBExecutionError(f"Splunk API error: {e}") from e
```

- [ ] **Step 3: 테스트 통과**
```bash
pytest tests/unit/test_splunk_client.py -v
```

- [ ] **Step 4: Commit**
```bash
git add app/infra/splunk/ tests/unit/test_splunk_client.py
git commit -m "feat: Splunk API client"
```

---

### Task 2: 패턴 분석기

**Files:**
- Create: `app/infra/splunk/pattern_analyzer.py`
- Create: `tests/unit/test_pattern_analyzer.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_pattern_analyzer.py
from app.infra.splunk.pattern_analyzer import PatternAnalyzer

def test_extract_error_patterns():
    analyzer = PatternAnalyzer()
    events = [
        {"_time": "2026-04-18T10:00:00", "message": "ERROR: sensor timeout EQP_A_001"},
        {"_time": "2026-04-18T10:01:00", "message": "ERROR: sensor timeout EQP_A_001"},
        {"_time": "2026-04-18T10:02:00", "message": "WARNING: high temperature"},
    ]
    patterns = analyzer.extract_patterns(events)
    assert len(patterns) > 0
    assert patterns[0]["count"] >= 2

def test_summarize_for_llm():
    analyzer = PatternAnalyzer()
    events = [{"_time": "2026-04-18T10:00:00", "message": "ERROR: valve stuck", "level": "ERROR"}]
    summary = analyzer.summarize_for_llm(events, max_events=10)
    assert "ERROR" in summary
    assert len(summary) > 0
```

- [ ] **Step 2: `app/infra/splunk/pattern_analyzer.py` 구현**

```python
from __future__ import annotations
import re
from collections import Counter


class PatternAnalyzer:
    def extract_patterns(self, events: list[dict]) -> list[dict]:
        messages = [e.get("message", "") for e in events]
        # 숫자/타임스탬프 제거 후 패턴화
        normalized = [re.sub(r"\d+", "N", m) for m in messages]
        counts = Counter(normalized)
        return [
            {"pattern": pat, "count": cnt, "sample": messages[normalized.index(pat)]}
            for pat, cnt in counts.most_common(10)
        ]

    def summarize_for_llm(self, events: list[dict], max_events: int = 20) -> str:
        lines = []
        for e in events[:max_events]:
            time = e.get("_time", "")
            level = e.get("level", "INFO")
            msg = e.get("message", "")
            lines.append(f"[{time}] [{level}] {msg}")
        patterns = self.extract_patterns(events)
        pattern_lines = [f"- {p['pattern']} (×{p['count']})" for p in patterns[:5]]
        return "=== 로그 요약 ===\n" + "\n".join(lines) + "\n\n=== 반복 패턴 ===\n" + "\n".join(pattern_lines)
```

- [ ] **Step 3: 테스트 통과 + Commit**
```bash
pytest tests/unit/test_pattern_analyzer.py -v
git add app/infra/splunk/pattern_analyzer.py tests/unit/test_pattern_analyzer.py
git commit -m "feat: Splunk log pattern analyzer"
```

---

## Week 2 (Week 6): SplunkAgent + 검토자 승인 플로우

### Task 3: DDL + 검토 Repository

**Files:**
- Create: `db/migrations/002_review_flow.sql`
- Create: `app/infra/db/review_repo.py`

- [ ] **Step 1: `db/migrations/002_review_flow.sql`**

```sql
CREATE TABLE pending_reviews (
  review_id      NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id     VARCHAR2(36),
  trace_id       VARCHAR2(36),
  message_id     NUMBER,
  agent_name     VARCHAR2(20),
  question       CLOB,
  analysis       CLOB,
  status         VARCHAR2(20) DEFAULT 'PENDING',  -- PENDING/APPROVED/REJECTED
  reviewer_id    VARCHAR2(50),
  reviewer_note  CLOB,
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP,
  reviewed_at    TIMESTAMP
);
CREATE INDEX idx_review_status ON pending_reviews(status, created_at);
```

- [ ] **Step 2: `app/infra/db/review_repo.py` 구현**

```python
from __future__ import annotations
import json
from app.infra.db.oracle import OraclePool
from app.shared.logging import get_logger

logger = get_logger(__name__)


class ReviewRepository:
    def __init__(self, app_pool: OraclePool):
        self.pool = app_pool

    async def create_pending(self, session_id: str, trace_id: str,
                              agent_name: str, question: str, analysis: dict) -> int:
        await self.pool.execute(
            "INSERT INTO pending_reviews (session_id, trace_id, agent_name, question, analysis)"
            " VALUES (:sid, :tid, :agent, :q, :analysis)",
            {
                "sid": session_id, "tid": trace_id, "agent": agent_name,
                "q": question, "analysis": json.dumps(analysis, ensure_ascii=False),
            },
        )
        rows = await self.pool.fetch_all(
            "SELECT MAX(review_id) AS rid FROM pending_reviews WHERE trace_id = :tid",
            {"tid": trace_id},
        )
        return rows[0].get("rid", 0) if rows else 0

    async def get_pending(self, limit: int = 20) -> list[dict]:
        return await self.pool.fetch_all(
            "SELECT review_id, session_id, agent_name, question, analysis, created_at"
            " FROM pending_reviews WHERE status = 'PENDING'"
            " ORDER BY created_at ASC FETCH FIRST :lim ROWS ONLY",
            {"lim": limit},
        )

    async def approve(self, review_id: int, reviewer_id: str, note: str = "") -> None:
        await self.pool.execute(
            "UPDATE pending_reviews SET status='APPROVED', reviewer_id=:rid,"
            " reviewer_note=:note, reviewed_at=SYSTIMESTAMP WHERE review_id=:id",
            {"rid": reviewer_id, "note": note, "id": review_id},
        )
```

- [ ] **Step 3: Commit**
```bash
git add db/migrations/002_review_flow.sql app/infra/db/review_repo.py
git commit -m "feat: review flow DDL + ReviewRepository"
```

---

### Task 4: SplunkAgent 구현

**Files:**
- Create: `config/prompts/splunk_query.j2`
- Create: `config/prompts/log_analysis.j2`
- Create: `app/core/agents/log/__init__.py`
- Create: `app/core/agents/log/agent.py`
- Create: `tests/integration/test_splunk_flow.py`

- [ ] **Step 1: 프롬프트 템플릿**

`config/prompts/splunk_query.j2`:
```
사용자 질문에서 Splunk 검색 조건을 추출하세요.

[질문]
{{ question }}

[사용 가능한 설비 ID 예시]
{{ eqp_candidates }}

JSON으로 출력:
{"eqp_id": "EQP_A_001", "time_range": "-24h", "keywords": ["ERROR", "timeout"]}
```

`config/prompts/log_analysis.j2`:
```
아래 로그를 분석하여 오동작 원인을 추정하세요.

[규칙]
1. 모든 주장에 [log_N] 형식 인용 포함
2. 확실하지 않으면 "추정" 명시
3. 원인이 불명확하면 needs_human_review: true

[질문]
{{ question }}

[로그 요약]
{{ log_summary }}

JSON으로 출력:
{
  "root_cause": "추정 원인",
  "evidence_logs": ["관련 로그"],
  "confidence": 0.0,
  "needs_human_review": false,
  "answer": "인용 포함 답변 [log_1]..."
}
```

- [ ] **Step 2: SplunkAgent 구현**

```python
# app/core/agents/log/agent.py
from __future__ import annotations
from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.infra.splunk.client import SplunkClient
from app.infra.splunk.pattern_analyzer import PatternAnalyzer
from app.infra.db.review_repo import ReviewRepository
from app.infra.llm.base import LLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, AgentResult, Evidence, Context
from app.shared.logging import get_logger

logger = get_logger(__name__)


@register
class SplunkAgent(Agent):
    name = "log"

    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer,
        splunk: SplunkClient,
        analyzer: PatternAnalyzer,
        review_repo: ReviewRepository,
        review_threshold: float = 0.7,
    ):
        self.llm = llm
        self.renderer = renderer
        self.splunk = splunk
        self.analyzer = analyzer
        self.review_repo = review_repo
        self.review_threshold = review_threshold

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        question = sub_query.query
        log = logger.bind(trace_id=context.trace_id, agent="log")

        # 1. 검색 조건 추출
        query_result = await self.llm.complete_json(
            self.renderer.render("splunk_query", question=question, eqp_candidates="")
        )
        eqp_id = query_result.get("eqp_id", "")
        time_range = query_result.get("time_range", "-24h")
        keywords = query_result.get("keywords", ["ERROR"])

        # 2. Splunk 검색
        splunk_query = f"index={self.splunk.index} host={eqp_id} ({' OR '.join(keywords)})"
        events = await self.splunk.search(splunk_query, earliest=time_range)

        if not events:
            return AgentResult(
                sub_query_id=sub_query.id, success=True,
                evidence=[], raw_data={"answer": "해당 기간 관련 로그가 없습니다", "events": []},
                confidence=0.0,
            )

        # 3. 패턴 분석
        log_summary = self.analyzer.summarize_for_llm(events)

        # 4. LLM 분석
        analysis = await self.llm.complete_json(
            self.renderer.render("log_analysis", question=question, log_summary=log_summary)
        )
        confidence = analysis.get("confidence", 0.0)
        answer = analysis.get("answer", "")

        evidences = [
            Evidence(
                id=f"log_{i+1}", source_type="log_line",
                content=e.get("message", ""),
                metadata={"time": e.get("_time", ""), "host": e.get("host", "")},
            )
            for i, e in enumerate(events[:10])
        ]

        # 5. 신뢰도 낮으면 검토자 승인 대기
        if confidence < self.review_threshold or analysis.get("needs_human_review"):
            await self.review_repo.create_pending(
                session_id=context.session_id,
                trace_id=context.trace_id,
                agent_name="log",
                question=question,
                analysis=analysis,
            )
            log.info("review_pending", confidence=confidence)
            return AgentResult(
                sub_query_id=sub_query.id, success=True,
                evidence=evidences,
                raw_data={"status": "pending_review", "answer": answer, "analysis": analysis},
                confidence=confidence,
            )

        log.info("splunk_complete", events=len(events), confidence=confidence)
        return AgentResult(
            sub_query_id=sub_query.id, success=True,
            evidence=evidences,
            raw_data={"answer": answer, "events": events[:10]},
            confidence=confidence,
        )
```

- [ ] **Step 3: 통합 테스트**

```python
# tests/integration/test_splunk_flow.py
import pytest
import respx
import httpx
from unittest.mock import AsyncMock
from app.core.agents.log.agent import SplunkAgent
from app.infra.splunk.client import SplunkClient
from app.infra.splunk.pattern_analyzer import PatternAnalyzer
from app.infra.llm.internal_api import InternalLLMProvider
from app.infra.llm.prompt_renderer import PromptRenderer
from app.shared.schemas import SubQuery, Context
import uuid


@pytest.fixture
def llm():
    return InternalLLMProvider(base_url="http://mock-llm", api_key="k", model="test")

@pytest.fixture
def renderer(tmp_path):
    (tmp_path / "splunk_query.j2").write_text("q: {{ question }}", encoding="utf-8")
    (tmp_path / "log_analysis.j2").write_text("q: {{ question }}\nlogs: {{ log_summary }}", encoding="utf-8")
    return PromptRenderer(prompt_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_splunk_agent_high_confidence_no_review(llm, renderer):
    splunk = SplunkClient(host="mock-splunk", port=8089, token="t", index="tc_events")
    analyzer = PatternAnalyzer()
    review_repo = AsyncMock()

    agent = SplunkAgent(llm=llm, renderer=renderer, splunk=splunk,
                        analyzer=analyzer, review_repo=review_repo, review_threshold=0.7)

    with respx.mock:
        respx.post("https://mock-splunk:8089/services/search/jobs").mock(
            return_value=httpx.Response(201, json={"sid": "j1"})
        )
        respx.get("https://mock-splunk:8089/services/search/jobs/j1/results").mock(
            return_value=httpx.Response(200, json={"results": [
                {"_time": "2026-04-18T10:00:00", "host": "EQP_A_001", "message": "ERROR: valve stuck", "level": "ERROR"}
            ]})
        )
        respx.post("http://mock-llm/chat/completions").mock(side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"eqp_id":"EQP_A_001","time_range":"-24h","keywords":["ERROR"]}'}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"root_cause":"valve stuck","evidence_logs":[],"confidence":0.85,"needs_human_review":false,"answer":"valve 고착 원인 [log_1]"}'}}]}),
        ])
        result = await agent.run(
            SubQuery(id="t1", agent="log", query="A 설비 오동작 원인?"),
            Context(session_id="s1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert result.raw_data.get("status") != "pending_review"
    review_repo.create_pending.assert_not_called()


@pytest.mark.asyncio
async def test_splunk_agent_low_confidence_triggers_review(llm, renderer):
    splunk = SplunkClient(host="mock-splunk", port=8089, token="t", index="tc_events")
    analyzer = PatternAnalyzer()
    review_repo = AsyncMock()

    agent = SplunkAgent(llm=llm, renderer=renderer, splunk=splunk,
                        analyzer=analyzer, review_repo=review_repo, review_threshold=0.7)

    with respx.mock:
        respx.post("https://mock-splunk:8089/services/search/jobs").mock(
            return_value=httpx.Response(201, json={"sid": "j2"})
        )
        respx.get("https://mock-splunk:8089/services/search/jobs/j2/results").mock(
            return_value=httpx.Response(200, json={"results": [
                {"_time": "2026-04-18T10:00:00", "host": "EQP_A_001", "message": "WARN: unknown state"}
            ]})
        )
        respx.post("http://mock-llm/chat/completions").mock(side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"eqp_id":"EQP_A_001","time_range":"-24h","keywords":["WARN"]}'}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"root_cause":"불명확","evidence_logs":[],"confidence":0.4,"needs_human_review":true,"answer":"원인 불명확 [log_1]"}'}}]}),
        ])
        result = await agent.run(
            SubQuery(id="t2", agent="log", query="A 설비 이상한데 왜그래?"),
            Context(session_id="s1", trace_id=str(uuid.uuid4())),
        )

    assert result.success
    assert result.raw_data.get("status") == "pending_review"
    review_repo.create_pending.assert_called_once()
```

- [ ] **Step 4: 테스트 통과**
```bash
pytest tests/unit tests/integration/test_splunk_flow.py -v
```

- [ ] **Step 5: Commit**
```bash
git add app/core/agents/log/ config/prompts/splunk_*.j2 config/prompts/log_analysis.j2 tests/integration/test_splunk_flow.py
git commit -m "feat: SplunkAgent with review flow"
```

---

## Week 3 (Week 7): 검토자 API + Golden Dataset + 통합

### Task 5: 검토자 API 엔드포인트

**Files:**
- Create: `app/api/v1/review.py`

```python
# app/api/v1/review.py
from fastapi import APIRouter, Depends
from app.infra.db.review_repo import ReviewRepository
from app.api.deps import get_review_repo

router = APIRouter()

@router.get("/reviews/pending")
async def list_pending(repo: ReviewRepository = Depends(get_review_repo)):
    return await repo.get_pending()

@router.post("/reviews/{review_id}/approve")
async def approve(review_id: int, reviewer_id: str,
                  note: str = "", repo: ReviewRepository = Depends(get_review_repo)):
    await repo.approve(review_id, reviewer_id, note)
    return {"status": "approved"}
```

- [ ] **Step 1: review.py 작성 후 main.py에 라우터 추가**
- [ ] **Step 2: deps.py에 get_review_repo 추가**
- [ ] **Step 3: Golden Dataset 작성** (`tests/golden/datasets/splunk_phase3.yaml`, 10개+)
- [ ] **Step 4: 전체 테스트 통과**
```bash
pytest tests/unit tests/integration -v
```
- [ ] **Step 5: Commit**
```bash
git add app/api/v1/review.py tests/golden/datasets/splunk_phase3.yaml
git commit -m "feat: Phase 3 complete — Splunk Agent + 검토자 승인 API"
git tag phase3-complete
```

---

## Self-Review

| 스펙 요구사항 | 구현 Task |
|------------|---------|
| Splunk API 어댑터 | Task 1 |
| 로그 패턴 분석 | Task 2 |
| 검토 대기 테이블 DDL | Task 3 |
| SplunkAgent (신뢰도 기반 자동/반자동) | Task 4 |
| 검토자 승인 API | Task 5 |
| Golden Dataset 10개+ | Task 5 |
