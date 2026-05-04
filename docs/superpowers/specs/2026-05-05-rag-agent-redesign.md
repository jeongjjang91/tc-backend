# RAG Agent 재설계 — 사내 RAG API 기반 + SOTA 기법 적용

- **작성일:** 2026-05-05
- **상태:** 설계 확정 (잔여 API 확인 항목 구현 시 반영)
- **관련 문서:**
  - `2026-04-18-tc-voc-chatbot-design.md` §6.1 — 원본 Doc Agent 설계 (이 문서로 대체)
  - `2026-04-29-rag-agent-internal-api-checklist.md` — 사내 API 확인 체크리스트
  - `2026-04-27-bge-m3-embedding-application-design.md` — BGE-M3 EmbeddingClient (SchemaStore 용도)

---

## 0. 변경 배경

원본 spec(§6.1)은 자체 OpenSearch + BGE-Reranker 호스팅을 가정했으나, 사내 RAG API 1차 확인 결과 이 모두를 API 단에서 통합 제공한다는 것이 파악됨.

| 원본 spec §6.1 가정 | 실제 확인 결과 | 결정 |
|---|---|---|
| BM25(OpenSearch) + Vector + RRF 자체 운영 | 사내 RAG API가 의미 검색 내장 | **OpenSearch 자체 운영 폐기** |
| BGE-Reranker-v2-m3 별도 호스팅 | RAG API 내부 reranker로 top-K 반환 | **별도 Reranker 호출 단계 제거** |
| Parent-Child Chunking 자체 인덱싱 | 반환 단위(청크/페이지) 잔여 확인 | 어댑터에서 처리 (§3 참조) |
| Adaptive Routing | Planner 레벨 결정 | **변동 없음** |
| BGE-M3 EmbeddingClient (RAG Agent 내) | RAG API가 자연어 직접 입력 | **RAG Agent에서 임베딩 호출 제거** |

**핵심 방향 전환:** 검색·reranking·인덱싱은 사내 RAG API에 위임. 우리 코드는 **쿼리 측 변환 + 컨텍스트 품질 향상 + Citation Enforcement**에 집중.

---

## 1. 확정 파이프라인

```
question
  └─[선택]─> QueryExpander            # Step-Back / Multi-Query (config OFF → Phase 2)
  └─> RAGClient (ABC)                 # 사내 RAG API, 자연어 → top-K 문서
  └─[선택]─> ChunkExpander            # 사내 API가 청크 반환 시 인접 청크 보강 (잔여 확인)
  └─[선택]─> ContextCompressor        # 청크 길이 초과 시 sentence-level 필터링 (Phase 2)
  └─> ContextReorderer                # Lost-in-the-middle 회피 재배치 (항상 실행)
  └─> LLM 답변 (ModelRouter — 대형)   # rag_answer.j2 + JSON Schema 강제
        └─> CitationValidator         # 인용 누락·형식 오류 시 재시도 1회
  └─> AgentResult                     # evidence + confidence + needs_human_review
```

**Phase 1 (즉시 적용):** RAGClient + ContextReorderer + Citation Enforcement + ModelRouter + Prompt Caching + SSE 스트리밍
**Phase 2 (성능 측정 후):** ContextCompressor + Adaptive K (CRAG-style) + LRU 캐시
**Phase 3 (필요 시):** QueryExpander (Step-Back / Multi-Query)

---

## 2. 제거되는 컴포넌트

| 기존 컴포넌트 | 이유 |
|---|---|
| `ConfluenceClient` | `RAGClient`로 대체 |
| `TFIDFReranker` | 사내 RAG API 내부 reranker로 대체 |
| `rag_query.j2` LLM 쿼리 재작성 | 자연어 직접 입력 가능 → 1 LLM 호출 절감 |
| BGE-M3 EmbeddingClient (RAG Agent 내) | 불필요. SchemaStore 용도로만 유지 |

---

## 3. 컴포넌트 설계

### 3-1. RAGClient (ABC + 구현체)

```
infra/rag/
  rag_client.py          # RAGClient ABC
  internal_rag_client.py # InternalRAGClient — 사내 RAG API 호출
  chunk_expander.py      # ChunkExpander (잔여 확인 후 필요 시)
  reranker.py            # TFIDFReranker 유지 — fallback 전용
```

```python
class RAGClient(ABC):
    @abstractmethod
    async def search(self, query: str, top_k: int, trace_id: str) -> list[Chunk]:
        ...

class InternalRAGClient(RAGClient):
    # 사내 RAG API POST /search
    # query: str, top_k: int → [{id, title, content, url, score?}]
    # fallback: ConfluenceClient (사내 API 다운 시)
```

**잔여 확인 항목별 분기:**

| 확인 결과 | 대응 |
|---|---|
| `top_k` 파라미터 가능 | `InternalRAGClient.search(top_k=N)` 그대로 |
| `top_k` 5 고정 | `top_k` 하드코딩, config로 노출 |
| 청크 단위 반환 | `ChunkExpander` 불필요 |
| 페이지 단위 반환 | `ChunkExpander`에서 단락 분리 후 LLM/BM25로 top-K 재선별 |
| score 노출 | `Chunk.score` 활용해 confidence 계산 |
| score 미노출 | 순서(rank) 기반 confidence 추정 |

### 3-2. ContextReorderer

Lost-in-the-middle 문제 완화. top-K를 score/rank 순에서 양 끝 배치로 재정렬.

```python
# [1위, 3위, 5위, 4위, 2위] — 가장 강한 것을 1번·마지막에
def reorder(chunks: list[Chunk]) -> list[Chunk]:
    evens = chunks[::2]   # 강한 것
    odds = chunks[1::2]   # 약한 것 (중간 배치)
    return evens[:1] + odds + evens[1:]
```

위치: `core/agents/rag/context_reorderer.py`

### 3-3. CitationValidator

`rag_answer.j2`에서 JSON 답변 생성 후 인용 형식(`[doc_N]`) 검증. 실패 시 재시도 1회.

```python
# core/agents/rag/citation_validator.py
class CitationValidator:
    def validate(self, answer: str, doc_count: int) -> bool:
        # [doc_1] ~ [doc_N] 형식이 최소 1개 이상 존재하는지 확인
```

### 3-4. RAGAgent (core)

```python
# core/agents/rag/agent.py
@register
class RAGAgent(Agent):
    name = "doc"

    def __init__(
        self,
        router: ModelRouter,
        renderer: PromptRenderer,
        rag_client: RAGClient,         # 주입 (ABC)
        reorderer: ContextReorderer,
        validator: CitationValidator,
        top_k: int = 5,
    ): ...

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        # 1. RAG API 검색
        # 2. ContextReorderer
        # 3. LLM 답변 (대형 모델, Prompt Caching)
        # 4. CitationValidator → 실패 시 재시도 1회
        # 5. AgentResult 패키징
```

**ModelRouter task 이름:** `"rag_answer"` → 대형 모델 (`llm_routing.yaml`에 추가)

---

## 4. 프롬프트 변경

### 4-1. `config/prompts/rag_answer.j2` 강화

```jinja2
아래 문서를 바탕으로 질문에 답하세요.

[규칙]
1. 모든 주장에 [doc_N] 형식의 인용 포함. 인용 없는 주장 금지.
2. 문서에 없는 내용 추가 금지.
3. 관련 문서가 없거나 신뢰도 낮으면 "문서에서 확인되지 않습니다" 답변.
4. 답변은 한국어로.

[질문]
{{ question }}

[문서]
{% for doc in docs %}
[doc_{{ loop.index }}] 제목: {{ doc.title }}
출처: {{ doc.url }}
{{ doc.content }}
{% endfor %}

JSON으로 출력 (schema 엄수):
{
  "answer": "인용 [doc_N] 포함 답변",
  "citations": ["doc_1", "doc_3"],
  "confidence": 0.0,
  "needs_human_review": false
}
```

`rag_query.j2` — **삭제** (LLM 재작성 단계 제거)

### 4-2. `config/rag.yaml` 신설

```yaml
top_k: 5
reorder_context: true
citation_retry_count: 1
confidence_threshold: 0.4   # 미만 시 needs_human_review: true
fallback_to_confluence: true

query_expander:
  enabled: false
  mode: step_back   # step_back | multi_query | hyde

context_compressor:
  enabled: false
  max_tokens_per_chunk: 800
```

---

## 5. 성능 개선 항목 우선순위

| 항목 | Phase | 기대 효과 |
|---|---|---|
| Citation Enforcement + 재시도 | 1 | 환각 차단, 평가 가능 |
| Lost-in-the-middle 재배치 | 1 | 긴 컨텍스트 정보 손실↓ |
| ModelRouter (rag_answer → 대형) | 1 | 답변 품질↑ |
| Prompt Caching (시스템 프롬프트) | 1 | 첫 토큰 latency 50%↓ |
| SSE 스트리밍 | 1 | 체감 latency 개선 |
| Adaptive K (confidence 낮으면 재호출) | 2 | recall↑, API 호출 1회 추가 |
| ContextCompressor (sentence 필터) | 2 | 토큰 절감 + 노이즈↓, +1 경량 LLM |
| In-memory LRU 캐시 (cachetools) | 2 | 동일 질문 반복 시 API 절감 |
| Step-Back Prompting | 3 | 구체적 질문 일반화, +1 LLM 비용 |
| Multi-Query / RAG-Fusion | 3 | 다각도 검색, N배 LLM 비용 |

---

## 6. Langchain / Langgraph 미도입 결정

| 프레임워크 | 결정 | 이유 |
|---|---|---|
| Langchain | **미도입** | `config/prompts/*.j2` 원칙과 `PromptTemplate` 충돌, `LLMProvider`·`ModelRouter` ABC와 이중 추상화, 단위 테스트 의존성 부담 |
| Langgraph | **미도입** | 현재 Planner+Agent 구조로 Agentic RAG 패턴 충족. checkpointing은 Redis 없이 ROI 낮음. Phase 2~3에서 다단계 분기 3개 이상 누적 시 재검토 |

Langchain 개념은 자체 구현으로 흡수:

| Langchain 개념 | 자체 구현 |
|---|---|
| `ContextualCompressionRetriever` | `ContextCompressor` (Phase 2) |
| `MultiQueryRetriever` | `QueryExpander` (Phase 3) |
| `ParentDocumentRetriever` | `ChunkExpander` (잔여 확인 후) |
| LangSmith tracing | `trace_id` + structlog (기존 구축) |

---

## 7. 디렉토리 구조

```
app/
├── core/agents/rag/
│   ├── agent.py              # RAGAgent (ABC 상속, AGENT_REGISTRY)
│   ├── context_reorderer.py  # Lost-in-the-middle 재배치
│   ├── citation_validator.py # 인용 형식 검증 + 재시도
│   └── query_expander.py     # (Phase 3) Step-Back / Multi-Query
├── infra/rag/
│   ├── rag_client.py         # RAGClient ABC
│   ├── internal_rag_client.py # 사내 RAG API 어댑터
│   ├── chunk_expander.py     # 페이지 반환 시 청크 분리 (잔여 확인 후)
│   └── reranker.py           # TFIDFReranker (fallback 전용 유지)
config/
├── prompts/
│   ├── rag_answer.j2         # Citation Enforcement 강화
│   └── [rag_query.j2 삭제]
└── rag.yaml                  # top_k, reorder, fallback, expander 설정
```

---

## 8. 잔여 확인 항목 (사내 API — 구현 착수 전 필수)

| 항목 | 영향 |
|---|---|
| `top_k` 파라미터 조정 가능 여부 | `config/rag.yaml`의 `top_k` 유효성 |
| 반환 단위 (청크 vs 페이지) | `ChunkExpander` 필요 여부 |
| `score` 필드 노출 여부 | confidence 계산 방식 |
| HTTP 메서드, 경로, 인증 방식 | `InternalRAGClient` 구현 |
| 장애 시 응답 형태 + fallback 정책 | Circuit breaker 설계 |
| ACL 처리 (권한 기반 결과 필터) | 보안 설계 |
| chunk_id 안정성 (페이지 이동 시) | Citation URL 영구성 |

---

## 9. Golden Eval 케이스 설계

| 시나리오 | 검증 항목 |
|---|---|
| 한국어 직접 질문 | 답변 존재 + 인용 [doc_N] 최소 1개 |
| 한영 혼용 질문 | 검색 결과 품질, 인용 정확성 |
| 정확 키워드 포함 질문 | top-1 문서 관련성 |
| 의미 매칭 질문 (동의어/약어) | recall, 인용 커버리지 |
| 문서에 없는 질문 | "문서에서 확인되지 않습니다" 반환 |
| confidence < threshold | `needs_human_review: true` 반환 |
| 인용 누락 → 재시도 | 재시도 후 인용 포함 확인 |
