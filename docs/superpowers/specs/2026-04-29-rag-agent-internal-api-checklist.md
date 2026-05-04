# RAG Agent 개선 설계 — 사내 API 확인 및 SOTA 기법 적용 계획

- 작성일: 2026-04-29
- 갱신: 2026-05-04 — 사내 RAG API 1차 확인 결과 반영
- 상태: 사내 API 1차 확인 완료 → 잔여 항목 확인 후 구현 착수

## 1차 확인 결과 (2026-05-04, 이전 spec 작업 시 파악)

- **RAG API 동작:** 자연어 질문 입력 → 내부 reranker로 **top-5 문서** 추출 → 문서 반환
- **함의:**
  - RAG API가 **검색 + reranking을 통합 제공**. 별도 Reranker API 호출 단계 불필요 (Section 3-2는 우선순위 하향)
  - 의미 검색 내장으로 추정 → 4. 파이프라인 분기 중 **Case A에 가까움**
  - 입력이 자연어 → BGE-M3 직접 호출 불필요 (RAG API 내부에서 처리)
  - top-5 고정인지 `top_k` 파라미터로 조정 가능한지 추가 확인 필요
- **확정 파이프라인 (잠정):**
  ```
  question → 사내 RAG API (top-5 문서) → LLM 답변 (Citation 강제)
  ```
  - 기존 LLM 쿼리 재작성 단계 제거 가능 → 1 LLM 호출 절감
  - `ConfluenceClient` → `RAGClient` 어댑터 교체
  - `TFIDFReranker` 제거
- 관련 문서:
  - `2026-04-18-tc-voc-chatbot-design.md` — RAG Agent 위치 및 인터페이스
  - `2026-04-27-bge-m3-embedding-application-design.md` — BGE-M3 EmbeddingClient
  - `2026-04-21-text-to-sql-improvement-and-eval-system.md` — T6, T9

---

## 0. 배경

현재 RAG Agent(`app/core/agents/rag/agent.py`)의 구조:

```
question → LLM 쿼리 재작성 → ConfluenceClient(REST 직접) → TFIDFReranker → LLM 답변
```

사내에 **RAG API**, **BGE-M3 Embedding API**, **Reranker API** 3개가 존재한다. 이를 활용하면 아래처럼 개선 가능하다:

```
question → [선택] 쿼리 변환 → 사내 RAG API → 사내 Reranker API → LLM 답변(인용 강제)
```

단, API 스펙에 따라 파이프라인 단계가 달라지므로 **사내 확인이 선행**되어야 한다.

---

## 1. 현재 구조의 약점

| 단계 | 현재 | 문제 |
|------|------|------|
| 쿼리 생성 | LLM 재작성 (`rag_query.j2`) | 1 LLM 호출 추가. 원본 질문이 나을 때도 있음 |
| Confluence 검색 | CQL `text~"query"` 키워드 | 한국어·혼용 표현에 약함 |
| 청크 분리 없음 | 페이지 전체가 하나의 chunk | 긴 문서에서 관련 부분 희석 |
| Reranker | TF-IDF char ngram (로컬) | 의미 검색 불가 |
| 인용 없음 | — | 설계 문서 Citation Enforcement 미구현 |

---

## 2. 이 프로젝트 구조와 SOTA RAG의 관계

최근 RAG 발전 단계는 `Naive RAG → Advanced RAG → Modular RAG → Agentic RAG`이다.

**Planner + Agent 구조는 이미 Agentic RAG 패턴**이다. SOTA 기법 대부분이 그대로 적용되며, 일부는 이미 구현되어 있다.

### 이미 구현된 SOTA 개념

| SOTA 개념 | 이 프로젝트 구현체 |
|-----------|------------------|
| Query Routing | Planner (prefilter + classifier + LLM fallback) |
| Query Decomposition | `Planner._parse_sub_queries()` mixed intent 분해 |
| CRAG-style Fallback | 애매한 분류 → Knowledge Agent |
| Confidence Routing | `confidence_threshold` → Review Queue |
| Modular Retrieval | DB / Doc / Log / Knowledge 데이터소스 분리 |

### 적용 위치별 SOTA 기법 매핑

| 기법 | 적용 위치 | 우선순위 |
|------|----------|---------|
| **Contextual Retrieval (Anthropic)** | 인덱싱 시점 (RAG API 인덱스 or 자체) | ★★★ |
| **Hybrid Retrieval** (Dense+Sparse RRF) | doc Agent 내부 | ★★★ |
| **Cross-encoder Reranking** | doc Agent 내부 (사내 Reranker API) | ★★★ |
| **Citation Enforcement** | `rag_answer.j2` + Synthesizer | ★★★ |
| **Lost-in-the-middle 회피** | Synthesizer evidence 재배치 | ★★ |
| **Step-Back Prompting** | doc Agent 쿼리 변환 (선택) | ★★ |
| **HyDE** | doc Agent 쿼리 변환 (선택) | ★ |
| **Multi-Query / RAG-Fusion** | doc Agent 내부 (LLM N배 비용) | ★ |
| Self-RAG | 파인튜닝 필요 — 적용 불가 | — |
| GraphRAG | 인프라 부담 큼, ROI 낮음 | — |

---

## 3. 사내 API 확인 체크리스트

### 3-1. RAG API

**핵심 확인 3가지 (시간 부족 시 이것만)**

- [x] 반환 단위가 **청크 단위**인가, 페이지 단위인가 → top-5 "문서" 단위 (청크/페이지 여부 추가 확인)
- [x] **의미 검색(BGE-M3 등) 내장**인가, 키워드 검색만인가 → reranker 내장 → 의미 검색 추정
- [x] 입력이 **자연어 텍스트**인가, 임베딩 벡터인가 → **자연어**

**상세 확인 항목 (잔여)**

- [ ] HTTP 메서드, 경로, 인증 방식
- [ ] timeout 권장값, rate limit (RPS)
- [ ] 검색 범위 필터 (space / project / 날짜 등)
- [ ] `top_k` 조정 가능 여부 (현재 5 고정인가, 파라미터인가, 최대값)
- [ ] 반환 단위가 **청크인지 페이지 전체인지** (Citation 입자도와 직결)
- [ ] 반환 필드 (`id`, `title`, `content`, `url`, `score`, `metadata`)
- [ ] score 노출 여부 및 의미 (코사인? reranker logit? 정규화?)
- [ ] 청크 단위면 청크 길이 정책 (글자 수 / 토큰 수)
- [ ] Contextual Retrieval (청크에 문서 맥락 prefix) 내장 여부
- [ ] 장애 시 응답 형태, Confluence REST fallback 가능 여부
- [ ] 인덱스 신선도 (Confluence 갱신 → 인덱스 반영까지 지연)
- [ ] 권한/ACL 처리 (인덱스 공개 범위, 호출자 권한 필터)
- [ ] chunk_id / page_id 안정성 (페이지 이동·이름 변경 시 유지)
- [ ] 한국어 / 한영 혼용 쿼리 품질
- [ ] 샘플 요청/응답 1세트 확보

### 3-2. Reranker API (우선순위 하향 — RAG API에 통합 확인)

> 1차 확인상 RAG API가 reranker 내장 → 별도 호출 불필요. 단, **자체 reranker로 교체·보강 여지** 있을 때만 검토.


**핵심 확인**

- [ ] 입력 형태: `{"query": str, "documents": [str, ...]}` 인지
- [ ] 반환 형태: 점수만(`[0.92, ...]`) 인지, 정렬된 결과(`[{"index": 2, "score": 0.92}]`) 인지

**상세 확인 항목**

- [ ] HTTP 메서드, 경로, 인증 방식
- [ ] 한 번에 보낼 수 있는 최대 문서 수
- [ ] 문서 길이 제한 (글자 수 / 토큰 수)
- [ ] `top_k` 파라미터 지원 여부
- [ ] 점수 범위 (0~1 정규화? 임의 실수?)
- [ ] 사용 모델 (BGE-reranker? 자체 모델?), 한국어 성능
- [ ] 응답 레이턴시 (10개 문서 기준)
- [ ] API 다운 시 fallback 정책 (TF-IDF 로컬? 원본 순서 유지?)

### 3-3. BGE-M3 Embedding API (RAG Agent용으로는 우선순위 하향)

> RAG API가 자연어 직접 입력 → RAG Agent에서는 별도 임베딩 호출 불필요. **SchemaStore(Text-to-SQL) 용도로만 확인**.

**핵심 확인**

- [ ] SchemaStore에서 쓰려는 `EmbeddingClient`와 **동일 엔드포인트**인가
- [x] ~~RAG API가 이미 내부적으로 사용하는가~~ → RAG API는 자연어 입력이라 무관

**상세 확인 항목**

- [ ] 단일 vs 배치 호출 지원
- [ ] 한 번에 보낼 수 있는 최대 텍스트 수, 길이 제한
- [ ] 임베딩 차원, 정규화 여부 (unit vector인가)
- [ ] dense / sparse / multi-vector 중 어느 것 반환
- [ ] 응답 레이턴시, 쿼터

---

## 4. 확인 결과에 따른 파이프라인 분기

> **2026-05-04 기준: Case A 유력.** 잔여 확인(반환 단위·top_k 조정·score 노출)이 끝나면 확정.

### Case A — RAG API가 의미 검색 내장 + 청크 단위 반환 (최선) ★ 유력

```
question → RAG API → Reranker API → LLM 답변 (Citation 강제)
```
- LLM 쿼리 재작성 단계 제거 (1 LLM 호출 절약)
- `ConfluenceClient` → `RAGClient` 어댑터 교체
- `TFIDFReranker` → `RerankerAPIClient` 교체

### Case B — RAG API가 키워드 검색만 지원

```
question → BGE-M3 임베딩 → RAG API (벡터 입력) → Reranker API → LLM 답변
```
- 또는 LLM 쿼리 재작성 유지 후 키워드로 검색

### Case C — RAG API가 페이지 단위 반환

```
question → RAG API → 청크 분리 (단락/고정 글자) → Reranker API → LLM 답변
```
- Contextual Retrieval 적용: 청크 분리 후 LLM으로 맥락 prefix 추가
- 인덱싱 1회성 비용, 운영 추가 비용 없음

### Case D — Reranker API 품질 낮음 / 사용 불가

- 기존 `TFIDFReranker` 유지
- 또는 BGE-M3 dense 점수 기반 자체 reranker 구현

---

## 5. 확인 후 진행할 작업

1. **`2026-04-30-rag-agent-redesign.md`** 작성
   - 확정된 파이프라인 다이어그램
   - `RAGClient` 어댑터 설계 (Case A/B/C 분기)
   - `RerankerAPIClient` 어댑터 설계
   - Contextual Retrieval 적용 방식 (RAG API 내장 vs 자체 인덱싱)
   - Citation Enforcement 설계 (`rag_answer.j2` JSON Schema)

2. **설정 파일 추가** — `config/rag.yaml`
   - `top_k`, `reranker_enabled`, fallback 정책

3. **Golden Eval 케이스 설계**
   - 한국어 / 한영 혼용 / 정확 키워드 / 의미 매칭 시나리오
   - Citation 포함 여부 검증
