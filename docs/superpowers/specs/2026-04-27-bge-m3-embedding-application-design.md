# bge-m3 임베딩 적용 설계

> 작성일: 2026-04-27
> 상태: 설계 (브레인스토밍 단계 완료)
> 관련 문서: `2026-04-21-text-to-sql-improvement-and-eval-system.md` (T1~T20), `2026-04-18-tc-voc-chatbot-design.md`

---

## 0. AI Agent를 위한 온보딩 지시

이 문서는 사내 **bge-m3** 임베딩 API 도입을 계기로, TC VOC 챗봇 백엔드 파이프라인의 어느 단계에 임베딩을 끼워 넣을지를 정리한 카탈로그 + 설계서입니다.

- 본 문서의 식별자는 `E1 ~ E8` (Embedding 적용 단위). T 번호와는 별도 네임스페이스.
- 각 적용 항목은 **`2026-04-21-text-to-sql-improvement-and-eval-system.md`의 T 번호 중 어느 것과 연관되는지** 표기. 이미 정의된 T 작업의 *내부 구현 보강*으로 묶이는 항목이 다수.
- 본 문서가 **새로 만드는 인프라**: `app/infra/llm/embedding_client.py` (가칭) — encode / encode_batch / 디스크 캐시.
- **이미 시작된 항목:** E2 (Planner Intent Classifier) — bge-m3 + Logistic Regression 분류기 적용 진행 중.

---

## 1. 배경

### 1-1. 도입 트리거

- 사내 인프라가 **bge-m3 임베딩 API**(배치 입력 지원)를 제공하기 시작.
- 기존 검토안이었던 SetFit / sentence-transformers 로컬 로드(~2GB 패키지, ~500MB RAM)는 **불필요**해짐.
- 사내 endpoint 사용으로 모델 버전·메모리·라이센스 관리가 외부화.

### 1-2. 적용 가치 영역

현재 파이프라인에서 의미 검색·분류·유사도 비교가 **TF-IDF / 룰 / 단순 hash**로 처리되는 지점이 다수. bge-m3로 교체하거나 확장하면:

- **정확도** — Schema Linking, Few-shot Retrieval, Anti-pattern 검출 등에서 char-ngram 한계 돌파.
- **사용자 체감 속도** — 의미 캐시(E4) hit 시 LLM 호출 우회, Planner LLM 호출 우회(E2).
- **운영 효율** — Active Learning 큐 클러스터링(E5), Self-Consistency 군집화(E7).

### 1-3. 본 문서가 다루지 않는 것

- 자체 임베딩 모델 학습/파인튜닝.
- 외부 Vector DB(Qdrant, Milvus) 도입 — `Redis/Kafka 등 추가 인프라 금지` 제약 유지.
- bge-m3 외 추가 임베딩 모델 동시 운영.
- RAG Agent 본구현 (Phase 2 별도 스펙).

---

## 2. bge-m3 인벤토리 & 공통 인터페이스

### 2-1. 모델/엔드포인트 가정

| 항목 | 값 (확인 필요) |
|------|---------------|
| 모델 | bge-m3 (multilingual, dense + sparse + multi-vector) |
| 임베딩 차원 | 1024 (dense) |
| 한국어 지원 | 강함 (다국어 retrieval SOTA) |
| 배치 입력 | **지원 확인됨** |
| 호출 방식 | 사내 HTTP API (auth 키 필요) |
| 응답 형식 | `{"embeddings": [[float, ...], ...]}` 가정 |

> **확인 액션:** 배치 최대 크기, RTT, rate limit, sparse/multi-vector 출력 노출 여부를 사내 인프라 팀에 1회 확인 후 본 문서 갱신.

### 2-2. 공통 클라이언트 — `app/infra/llm/embedding_client.py` (신규)

```python
# app/infra/llm/embedding_client.py

from __future__ import annotations
import asyncio
import hashlib
from pathlib import Path
import httpx
import numpy as np
from app.shared.exceptions import LLMError
from app.shared.logging import get_logger

logger = get_logger(__name__)


class EmbeddingClient:
    """bge-m3 사내 API 래퍼. 단건/배치 + 디스크 캐시."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "bge-m3",
        cache_dir: Path | None = None,
        timeout: float = 30.0,
        max_batch: int = 256,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.cache_dir = cache_dir
        self.max_batch = max_batch
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def encode(self, text: str) -> np.ndarray:
        vecs = await self.encode_batch([text])
        return vecs[0]

    async def encode_batch(self, texts: list[str]) -> np.ndarray:
        """texts → (N, D) numpy array. 내부적으로 max_batch 단위 분할."""
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        chunks = [texts[i : i + self.max_batch] for i in range(0, len(texts), self.max_batch)]
        results = await asyncio.gather(*(self._encode_one(c) for c in chunks))
        return np.vstack(results)

    async def _encode_one(self, batch: list[str]) -> np.ndarray:
        try:
            resp = await self._client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": batch},
            )
            resp.raise_for_status()
            return np.asarray(resp.json()["embeddings"], dtype=np.float32)
        except httpx.HTTPError as e:
            raise LLMError(f"Embedding API error: {e}") from e

    # --- 디스크 캐시 (콘텐츠 해시 키) ---
    def cache_key(self, texts: list[str]) -> str:
        h = hashlib.sha256()
        for t in texts:
            h.update(t.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:16]

    async def encode_batch_cached(self, texts: list[str], scope: str) -> np.ndarray:
        """동일 텍스트 셋(예: 시드 예시)의 재인코딩 방지."""
        if self.cache_dir is None:
            return await self.encode_batch(texts)
        key = self.cache_key(texts)
        path = self.cache_dir / f"{scope}_{key}.npy"
        if path.exists():
            logger.info("embedding_cache_hit", scope=scope, key=key)
            return np.load(path)
        vecs = await self.encode_batch(texts)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, vecs)
        logger.info("embedding_cache_miss_saved", scope=scope, key=key, n=len(texts))
        return vecs
```

**설계 메모:**
- **단건/배치 동일 인터페이스** — 호출부는 `encode` 또는 `encode_batch`만 보면 됨.
- **콘텐츠 해시 캐시** — 시드 예시·스키마 description처럼 자주 안 바뀌는 텍스트는 디스크 `.npy`로 저장. 기동 시 재호출 비용 0.
- **추가 인프라 금지 준수** — 캐시는 로컬 디스크. Redis 등 외부 KV 사용 안 함.

### 2-3. 환경변수 (`.env.example` 추가)

```bash
# === Embedding (bge-m3) ===
EMBED_API_BASE_URL=http://internal-embed-api/v1
EMBED_API_KEY=<secret>
EMBED_MODEL=bge-m3
EMBED_CACHE_DIR=.cache/embeddings   # 디스크 직렬화 위치 (gitignore)
EMBED_MAX_BATCH=256
```

---

## 3. 적용 후보 카탈로그

각 항목 헤더의 `[T-xx]` 는 `2026-04-21-...` 문서의 T 번호 참조.

### 적용 우선순위 한눈에

| ID | 적용처 | 관련 T | 상태 | Impact | Effort | Phase |
|----|--------|-------|------|--------|--------|-------|
| **E1** | `schema_store` 의미 검색 | T5, T11 | 신규 | ★★★ | 3h | A |
| **E2** | Planner Intent Classifier | T18, T19 | **진행 중** | ★★★ | 4h (마무리) | A |
| **E3** | `few_shot_store` 의미 retrieval | T4, T11 | 신규 | ★★★ | 3h | A |
| **E4** | Query Log 의미 캐시 | T12 | 신규 | ★★★ | 6h | B |
| **E5** | Active Learning 클러스터링 | T15 | 신규 | ★★ | 4h | C |
| **E6** | Anti-pattern 의미 유사도 | T14 | 신규 | ★★ | 3h | B |
| **E7** | Self-Consistency 후보 군집화 | T8 | 신규 | ★★ | 3h | C |
| **E8** | RAG Reranker | (Phase 2) | 신규 | ★★ | 4h | D |

---

### E1. schema_store 의미 검색 [관련 T5, T11]

**현재 상태 (`app/infra/db/schema_store.py:28~39`):**
```python
self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
self._matrix = self._vectorizer.fit_transform(docs)
...
def search(self, query, top_k=5):
    q_vec = self._vectorizer.transform([query])
    # cosine sim 계산 → top-k 반환
```

**한계:** char-ngram은 어휘 표면 일치만 잡음. "설비 가동률" ↔ "라인 utilization" 같은 의미 동치 매칭 불가.

**제안 변경:** 인덱스를 bge-m3로 교체. `search` 시그니처 유지 → 호출부 무변경.

```python
# app/infra/db/schema_store.py (개선판)

class SchemaStore:
    def __init__(self, embedder: EmbeddingClient):
        self._embedder = embedder
        self._docs: list[dict] = []
        self._matrix: np.ndarray | None = None  # (N, 1024)

    async def index(self, schema_yaml_path: str) -> None:
        self._docs = self._load_docs(schema_yaml_path)
        texts = [self._doc_to_text(d) for d in self._docs]  # description + table/column 결합
        self._matrix = await self._embedder.encode_batch_cached(texts, scope="schema")

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        q = await self._embedder.encode(query)
        # cosine: 정규화 후 dot product
        q_n = q / (np.linalg.norm(q) + 1e-8)
        m_n = self._matrix / (np.linalg.norm(self._matrix, axis=1, keepdims=True) + 1e-8)
        scores = m_n @ q_n
        idx = np.argsort(-scores)[:top_k]
        return [{**self._docs[i], "score": float(scores[i])} for i in idx]
```

**T5 (description 품질 개선) 와의 관계:** description이 잘 써져 있을수록 임베딩 품질 ↑. T5 작업 결과를 그대로 임베딩 입력으로 사용. `scripts/check_schema_descriptions.py`(T5 신규) 통과한 description을 인덱싱.

**T11 (Schema Linker 2단계 재랭킹) 와의 관계:** T11의 1단계가 TF-IDF였다면 → bge-m3로 교체. 2단계 LLM 재랭킹은 그대로. **즉 E1 = T11 stage-1 구현체.**

**Cold Start:** 테이블 N개 × 평균 컬럼 M개 ≈ 수백~수천 doc. 배치 256 단위로 1~3회 호출, ~1초. 캐시 hit 시 0.

**TF-IDF Fallback:** bge-m3 API 장애 시 기존 TF-IDF 인덱스로 자동 fallback (양쪽 인덱스 보유). `EmbeddingClient`가 `LLMError` 던지면 TF-IDF 결과 사용.

---

### E2. Planner Intent Classifier [관련 T18, T19] — 진행 중

**현재 상태:** 사용자가 이미 구현 진행 중. 구조는 다음과 같음:

```
prefilter (rule 기반 exclusion)
   ↓ (그 외)
bge-m3 encode → Logistic Regression → (label, prob)
   ↓ (top1 < θ_high OR margin < θ_margin)
LLM fallback (Gemma3, β 라우팅)
```

**T18 (Pre-filter + SmallTalkAgent) 와의 관계:** T18의 prefilter 단계 = 본 구조의 tier 1.
**T19 (Rule 신뢰도 + 경량 LLM 분류기) 와의 관계:** T19의 "rule confidence + LLM 위임" 구조를 → "rule prefilter + bge-m3 classifier + LLM fallback" 으로 **재설계**한 결과가 E2. T19v2로 부를 수도 있으나 본 문서에서는 E2로 지칭.

**LR vs Centroid (구현 노트):**
- Logistic Regression이 디폴트 (보정된 확률 → 임계값 룰과 정합).
- Centroid (cosine to class-mean) 는 **ablation baseline**으로 동시 유지. `config/planner.yaml: classifier_mode: lr | centroid`.
- Golden Eval에서 두 모드 모두 측정 → Macro-F1 / ECE / LLM fallback rate 비교.

**임계값 (이 문서 기준):**
| 파라미터 | 값 | 비고 |
|---------|---|------|
| `θ_high` (top1 확률) | 0.80 | calibration 후 기준 |
| `θ_margin` (top1 - top2) | 0.15 | 마진 부족이면 fallback |
| LLM fallback rate 목표 | ≤ 20% | 초과 시 시드 보강 신호 |

**Mixed Intent 후보 detect:**
- `top1 prob ≥ 0.5` 이면서 `top2 prob ≥ 0.4` → mixed 의심 → LLM Decomposer 호출.
- 분해 결과는 `list[SubQuery]` 로 반환 (기존 시그니처 호환).

**파일 변경 예상:**
- `app/core/orchestrator/intent_classifier.py` (신규)
- `app/core/orchestrator/planner.py` (수정 — tier 통합)
- `config/planner.yaml` (신규 — 임계값, classifier_mode, seed 경로)
- `config/planner_seeds.yaml` (신규 — class당 20~30 시드)
- `config/prompts/planner_decompose.j2` (신규 — mixed intent 분해)
- `tests/unit/test_intent_classifier.py` (신규)

---

### E3. few_shot_store 의미 기반 retrieval [관련 T4, T11]

**현재 상태 (`app/infra/db/few_shot_store.py:39~46`):**
```python
self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
...
def search(self, question, top_k=3):
    q_vec = self._vectorizer.transform([skeleton])  # skeleton = 정규화된 질문
```

**한계:** 질문 표면 형태 매칭. SQL 패턴이 같아도 표현이 다르면 못 찾음.

**제안 변경:** 임베딩 + cosine 매칭. 추가로 **MMR(Maximal Marginal Relevance)** 로 다양성 확보 → ICL 품질 향상.

```python
async def search(self, question: str, top_k: int = 3, mmr_lambda: float = 0.7) -> list[dict]:
    q = await self._embedder.encode(question)
    sims = self._cosine(q, self._matrix)  # (N,)
    if mmr_lambda >= 1.0:
        idx = np.argsort(-sims)[:top_k]
    else:
        idx = self._mmr_select(q, sims, top_k, mmr_lambda)
    return [self._examples[i] for i in idx]
```

**T4 (Few-shot Seed 확장) 와의 관계:** T4에서 추가한 20+ 시드 패턴이 그대로 인덱싱 대상. 시드는 `add_seed()` 호출 → 디스크 캐시. 운영 중 누적되는 `add_success(question, sql)` 는 **증분 인덱스** — 메모리 vector list에 push, 일정 주기로 디스크 직렬화.

**MMR 효과:** 비슷한 SQL 패턴이 K개 모두 들어가는 것을 방지. 동일 카테고리 1~2개 + 다른 패턴 1~2개 → LLM이 일반화하기 좋음.

**Cold Start:** 시드 ~100개 → 1배치, ~200ms. 캐시 hit 시 0.

---

### E4. Query Log 의미 캐시 [관련 T12]

**현재 상태 (T12 도입 예정):** `query_log` 테이블에 `question_hash` (md5) 컬럼 — exact 매칭 캐시. 표현이 조금만 달라도 hit 안 됨.

**제안 변경:** **의미 캐시 레이어** 추가.

#### 4-1. DDL 확장 (T12 마이그레이션 위에 누적)

```sql
-- db/migrations/008_query_log_semantic_cache.sql

ALTER TABLE query_log
    ADD COLUMN question_embedding BLOB NULL  -- 1024 float32 = 4096 bytes
    ;

CREATE INDEX idx_query_log_success ON query_log (success, created_at);
-- 의미 캐시 검색 대상은 success=1 행만
```

#### 4-2. 캐시 조회 흐름

```python
# app/infra/db/query_log.py (수정)

async def get_cached_sql_semantic(self, question: str, threshold: float = 0.92) -> dict | None:
    q_emb = await self._embedder.encode(question)
    rows = await self.pool.fetch_all(
        "SELECT question, sql, question_embedding FROM query_log WHERE success=1 ORDER BY created_at DESC LIMIT 5000"
    )
    if not rows:
        return None
    embs = np.stack([np.frombuffer(r["question_embedding"], dtype=np.float32) for r in rows])
    sims = self._cosine(q_emb, embs)  # (N,)
    best = int(np.argmax(sims))
    if sims[best] >= threshold:
        return {"sql": rows[best]["sql"], "matched_question": rows[best]["question"], "sim": float(sims[best])}
    return None
```

#### 4-3. 검증 단계 (필수)

의미 매칭이 정확해도 SQL 그대로 재실행은 위험. 다음 검증 후 사용자에게 노출:
1. **SQL 화이트리스트 재검증** (`SQLValidator`).
2. **재실행** — 결과가 비어 있지 않으면 채택. 비어 있으면 LLM 경로로 진입.
3. **재실행 결과 = 캐시된 결과 set 일치도 ≥ 0.95** 이어야 캐시 답변 그대로 노출. 아니면 재생성.

#### 4-4. 부트스트랩 (배치 입력의 핵심 가치)

기존 누적 `query_log` 가 N행 있다고 하면:
- N=10,000 → 배치 256 단위 × 39회 ≈ 8초 (네트워크 효율 높음).
- 단건 호출이면 N × ~50ms = 500초+. 사실상 못 함.

```python
# scripts/bootstrap_query_log_embeddings.py (신규)

async def main():
    rows = await pool.fetch_all("SELECT id, question FROM query_log WHERE success=1 AND question_embedding IS NULL")
    for chunk in chunked(rows, 256):
        embs = await embedder.encode_batch([r["question"] for r in chunk])
        await pool.execute_many(
            "UPDATE query_log SET question_embedding=:emb WHERE id=:id",
            [{"id": r["id"], "emb": e.tobytes()} for r, e in zip(chunk, embs)],
        )
        log.info("bootstrap_progress", done=len(chunk))
```

#### 4-5. 기대 효과

| 지표 | 현재 (exact) | E4 적용 후 | 비고 |
|------|-------------|-----------|------|
| Cache hit rate | ~10% | ~30~40% | 표현 변형 흡수 |
| Hit 시 응답시간 | <100ms | <200ms (임베딩 1번 + 검증 재실행) | 여전히 LLM 전체보다 압도적 |
| LLM 호출 절감 | - | **20~30%↓** | 토큰 비용 + 체감 속도 |

#### 4-6. 위험과 대응

- **거짓 hit** — 의미 비슷하나 답이 달라야 하는 경우. → 4-3 검증 단계로 차단. 차단된 케이스를 `eval_case` 로 회귀 추가.
- **인덱스 크기** — 1024 dim × 4byte × 10K = 40MB. 메모리 부담 적음. 100K 넘으면 ANN(faiss-cpu) 도입 검토 — 다만 본 spec scope 밖.
- **bge-m3 버전 변경** — 임베딩 호환성 깨짐. `query_log` 행에 `embed_model_version` 컬럼 추가 권장.

---

### E5. Active Learning 클러스터링 [관련 T15]

**T15 현재 안:** 저신뢰 케이스를 `active_learning_queue` 테이블에 raw 적재 → 운영자 한 건씩 검토.

**제안 변경:** 큐 적재 시 **임베딩도 같이 저장** → 운영자 화면에서 **클러스터별 묶음 처리**.

#### 5-1. DDL 확장

```sql
ALTER TABLE active_learning_queue
    ADD COLUMN question_embedding BLOB NULL,
    ADD COLUMN cluster_id INT NULL;
```

#### 5-2. 클러스터링 잡 (`scripts/cluster_al_queue.py` 신규)

- 미라벨 행 N개 → 배치 임베딩.
- KMeans (k = max(5, N/10)) 또는 HDBSCAN.
- `cluster_id` 부여 → 운영자 화면에서 cluster별 정렬.

**효과:** 비슷한 오분류·저신뢰 케이스를 한 번에 라벨링 → 처리량 N배. 또한 클러스터 중심 케이스만 보면 시드/anti-pattern 보강 우선순위 명확.

**의존:** T15 인프라(테이블, 운영자 화면)가 먼저 있어야 함. → Phase C.

---

### E6. Anti-pattern 의미 유사도 [관련 T14]

**T14 현재 안:** `config/few_shot/sql_antipatterns.yaml` 에 안티패턴 SQL + 설명. 룰/문자열 매칭으로 차단.

**한계:** 표현이 조금만 달라도 매칭 실패. 예: `SELECT * FROM TC_EQP_PARAM` (안티) vs `SELECT * FROM tc_eqp_param WHERE 1=1` (실질 동일).

**제안 변경:** 안티패턴 SQL을 임베딩 → 생성 SQL과 cosine 유사도 검사.

```python
# app/core/agents/db/anti_pattern_checker.py (신규)

class AntiPatternChecker:
    async def init(self, anti_patterns: list[dict]):
        texts = [p["sql"] for p in anti_patterns]
        self._matrix = await self._embedder.encode_batch_cached(texts, scope="anti_patterns")
        self._patterns = anti_patterns

    async def check(self, generated_sql: str, threshold: float = 0.88) -> dict | None:
        emb = await self._embedder.encode(generated_sql)
        sims = self._cosine(emb, self._matrix)
        best = int(np.argmax(sims))
        if sims[best] >= threshold:
            return {**self._patterns[best], "sim": float(sims[best])}
        return None
```

**통합 위치:** `SQLValidator.validate_and_fix()` 직후 또는 `SQLGenerator.generate()` 이후. 매칭 시 → Refiner에 "anti-pattern X와 유사" 컨텍스트 전달 → 재생성.

**Cold Start:** 안티패턴 ~30개 → 1배치, <100ms.

---

### E7. Self-Consistency 후보 군집화 [관련 T8]

**T8 현재 안:** SQL 후보 N=3~5개 생성 → **실행 결과 hash** 다수결.

**한계:** 결과는 같지만 SQL 표현이 완전히 다른 후보(예: subquery vs JOIN) 가 동일 가중치. 잘못된 결과를 우연히 다수가 만들면 채택.

**제안 변경:** SQL 후보를 **임베딩으로도 군집화** → 결과 + 표현 양쪽 일치할 때 가중치 ↑.

```python
async def vote(self, candidates: list[str], execution_results: list[frozenset]) -> str:
    # 1) 실행 결과 일치 그룹
    result_groups = group_by(candidates, key=lambda i: hash(execution_results[i]))
    # 2) 같은 그룹 내에서 SQL 임베딩 군집 분포 확인
    embs = await self._embedder.encode_batch(candidates)
    sims = self._pairwise_cosine(embs)
    # 3) 결과 그룹 + 평균 표현 유사도 → 가중 점수
    scores = {gid: len(g) * (1 + avg_intra_sim(g, sims)) for gid, g in result_groups.items()}
    best_gid = max(scores, key=scores.get)
    return candidates[result_groups[best_gid][0]]
```

**기대 효과:** 결과는 같지만 우연히 잘못된 SQL이 다수가 되는 케이스를 약화. 표현이 다양한 동등 SQL은 여전히 가중치 정상 부여.

**의존:** T8 (Self-Consistency) 가 먼저 구현되어야 함. → Phase C.

---

### E8. RAG Reranker (Phase 2 별도 트랙)

**현재 상태:** `app/infra/rag/reranker.py` 가 존재하나 Phase 2 본격화 시 본구현. 현재는 placeholder 가능성.

**제안:** bge-m3의 dense + multi-vector 조합으로 사내 RAG 1차 검색 결과 reranking. 본 문서에서는 **포인터만** — 상세 설계는 RAG Agent 본 spec 시에 별도.

---

## 4. 권장 구현 단계 (Phase A~D)

| Phase | 항목 | 의존 | 예상 누적 시간 | 게이트 |
|-------|------|------|--------------|--------|
| **A** | EmbeddingClient + E1 + E2 + E3 | bge-m3 endpoint 확정 | ~12h | Golden Eval baseline 유지·향상 |
| **B** | E4 + E6 | T12 / T14 마이그레이션 완료 | +9h | cache hit rate 측정·anti-pattern 차단 사례 검증 |
| **C** | E5 + E7 | T8 / T15 인프라 완료 | +7h | 운영 사용자 피드백·SC 정확도 |
| **D** | E8 | RAG Agent 본 spec | TBD | 별도 트랙 |

**Phase A 우선 이유:**
- E1·E3 는 인터페이스 무변경 — 호출부 안전.
- E2 는 이미 진행 중 — 마무리만.
- 셋 다 SQL 정확도 직결 → Golden Eval 즉시 측정 가능.
- Phase B/C는 Phase A의 클라이언트·캐시 인프라를 그대로 재사용.

---

## 5. 운영 고려사항

### 5-1. Cold Start 시간 (배치 효과)

| 인덱스 | 항목 수 | 배치 (256) 호출 횟수 | 예상 시간 (RTT 200ms 기준) |
|--------|--------|-------------------|------------------------|
| Schema (E1) | ~1,000 | 4 | ~0.8s |
| Few-shot 시드 (E3) | ~100 | 1 | ~0.2s |
| Planner 시드 (E2) | ~100 | 1 | ~0.2s |
| Anti-pattern (E6) | ~30 | 1 | ~0.2s |
| **합계 (캐시 miss)** | - | 7 | **~1.4s** |
| 캐시 hit 시 | - | 0 | **~0s** |

→ 디스크 캐시(`.cache/embeddings/*.npy`) 직렬화로 콜드 스타트 비용 0에 수렴.

### 5-2. 메모리

| 인덱스 | 차원 × 항목 × 4byte | 메모리 |
|--------|------------------|------|
| Schema | 1024 × 1000 × 4 | ~4 MB |
| Few-shot | 1024 × 100 × 4 | ~0.4 MB |
| Planner seeds | 1024 × 100 × 4 | ~0.4 MB |
| Anti-pattern | 1024 × 30 × 4 | ~0.12 MB |
| Query Log (E4, in-memory cache 5K) | 1024 × 5000 × 4 | ~20 MB |
| **합계** | - | **~25 MB** |

→ 무시할 수준. ANN/별도 vector DB 불필요.

### 5-3. 장애 대응

| 시나리오 | 동작 |
|---------|------|
| bge-m3 API 일시 장애 | E1/E3: TF-IDF 인덱스 fallback / E2: rule + LLM fallback / E4: 캐시 miss로 처리 |
| 임베딩 차원 변경 | 디스크 캐시 무효화 (해시 키에 model 버전 포함) |
| 캐시 손상 | 자동 재인코딩 |
| API rate limit | `EmbeddingClient`에 지수 백오프 추가 (별도 PR) |

### 5-4. 모니터링 지표 (`eval_case` 컬럼 또는 별도 metrics 테이블)

| 지표 | 설명 | 임계 |
|------|------|------|
| `embed_call_count` | 요청당 임베딩 호출 수 | 평균 ≤ 2 |
| `cache_hit_rate_e4` | 의미 캐시 hit 비율 | ≥ 25% |
| `planner_llm_fallback_rate` | E2 → LLM 위임 비율 | ≤ 20% |
| `anti_pattern_blocked_count` | E6 차단 횟수 | 추세 모니터 |
| `embed_api_error_rate` | bge-m3 호출 실패율 | < 1% |

---

## 6. 평가 / Ablation

### 6-1. Golden Eval 측정 항목 (E별)

| ID | 측정 지표 | 비교 |
|----|----------|------|
| E1 | Schema Linking Recall@5, EX Score | TF-IDF baseline vs bge-m3 |
| E2 | Macro-F1, ECE, LLM fallback rate | 기존 LLM-only vs E2 (LR) vs E2 (centroid) |
| E3 | EX Score, ICL example diversity (intra-similarity) | TF-IDF vs bge-m3 vs bge-m3+MMR |
| E4 | Cache hit rate, false-positive 차단율 | exact-only vs +semantic |
| E5 | 라벨링 처리량 (cases/hour) | 클러스터 전 vs 후 — 운영 측정 |
| E6 | Anti-pattern 차단 precision/recall | 룰 vs 임베딩 |
| E7 | SC voting 정확도 (특히 결과-동일·SQL-상이 케이스) | 결과 hash only vs +임베딩 가중 |

### 6-2. Ablation Study 표 (`2026-04-21-...` 의 표에 누적)

```
| 구성 | EX Score | Valid SQL % | Hard EX | Latency(ms) |
|------|----------|-------------|---------|-------------|
| ...  | ...      | ...         | ...     | ...         |
| + bge-m3 schema_store (E1) | ? | ? | ? | ? |
| + bge-m3 few_shot_store (E3) | ? | ? | ? | ? |
| + Planner E2 (LR)            | ? | ? | ? | -1500ms (Planner LLM 제거) |
| + Query Log semantic cache (E4) | ? | ? | ? | -large% (hit 시) |
| + Anti-pattern semantic (E6) | ? | ? | ? | ? |
| + SC w/ embedding (E7)       | ? | ? | ? | ? |
```

각 줄은 별도 git 브랜치에서 측정 → `eval_run.git_sha` 로 추적 (T1 인프라 재사용).

---

## 7. 파일 변경 요약

| 파일 | 변경 유형 | 적용 |
|------|----------|------|
| `app/infra/llm/embedding_client.py` | 신규 (`EmbeddingClient`) | 공통 |
| `app/api/deps.py` | 수정 (EmbeddingClient 등록·주입) | 공통 |
| `.env.example` | 수정 (`EMBED_*` 5개 변수) | 공통 |
| `.gitignore` | 수정 (`.cache/embeddings/`) | 공통 |
| `app/infra/db/schema_store.py` | 수정 (TF-IDF → bge-m3, fallback 보유) | E1 |
| `tests/unit/test_schema_store_embedding.py` | 신규 | E1 |
| `app/core/orchestrator/intent_classifier.py` | 신규 (`IntentClassifier`, LR + centroid) | E2 |
| `app/core/orchestrator/planner.py` | 수정 (3-tier 통합) | E2 |
| `config/planner.yaml` | 신규 (임계값, classifier_mode) | E2 |
| `config/planner_seeds.yaml` | 신규 (class당 시드) | E2 |
| `config/prompts/planner_decompose.j2` | 신규 (mixed intent 분해) | E2 |
| `tests/unit/test_intent_classifier.py` | 신규 | E2 |
| `app/infra/db/few_shot_store.py` | 수정 (bge-m3 + MMR) | E3 |
| `tests/unit/test_few_shot_mmr.py` | 신규 | E3 |
| `db/migrations/008_query_log_semantic_cache.sql` | 신규 | E4 |
| `app/infra/db/query_log.py` | 수정 (의미 캐시 메서드 추가) | E4 |
| `scripts/bootstrap_query_log_embeddings.py` | 신규 | E4 |
| `tests/integration/test_semantic_cache.py` | 신규 | E4 |
| `db/migrations/009_al_queue_embedding.sql` | 신규 | E5 |
| `scripts/cluster_al_queue.py` | 신규 | E5 |
| `app/core/agents/db/anti_pattern_checker.py` | 신규 | E6 |
| `app/core/agents/db/agent.py` | 수정 (AntiPatternChecker 호출) | E6 |
| `app/core/agents/db/agent.py` | 수정 (SC voting 가중치) | E7 |

---

## 8. T 번호와의 관계 — 한 페이지 매핑

| 본 문서 | 관련 T | 관계 |
|--------|-------|------|
| E1 | **T5** (description 품질) | 입력 품질 의존 |
| E1 | **T11** (Schema Linker 2단계) | E1 = T11 stage-1 구현체 |
| E2 | **T18** (Pre-filter + SmallTalkAgent) | E2 tier 1 = T18 prefilter |
| E2 | **T19** (Rule 신뢰도 + 경량 LLM 분류기) | E2 가 T19를 재설계 (T19v2) |
| E3 | **T4** (Few-shot Seed 확장) | T4 시드를 인덱싱 대상으로 |
| E3 | **T11** | Few-shot도 retrieval 품질이 SQL 정확도에 영향 |
| E4 | **T12** (Query Log 캐싱) | T12 hash 캐시 위에 의미 레이어 |
| E5 | **T15** (Active Learning Loop) | T15 큐에 임베딩·클러스터 추가 |
| E6 | **T14** (Anti-pattern Few-shot YAML) | YAML 패턴을 임베딩으로 확장 |
| E7 | **T8** (Self-Consistency) | 결과 hash 다수결에 임베딩 군집 가중 |
| E8 | (Phase 2 RAG Agent 별도 spec) | 본 문서 범위 밖 |

---

## 9. Out of Scope

- 임베딩 모델 자체 학습/파인튜닝.
- 외부 Vector DB 도입 (Qdrant, Milvus, pgvector 등) — 현재 데이터 규모(~25MB)에서 불필요. 100K행 넘어가면 별도 검토.
- Cross-encoder reranker (cohere-rerank 등) — bge-m3 multi-vector로 대체.
- Streaming embedding (long document chunking) — RAG Agent spec에서 다룸.
- 임베딩 기반 Few-shot **자동 생성** — Active Learning(T15) 루프와 별개 트랙.

---

## 10. 결정 필요 (이 문서를 fix 하기 전)

| # | 질문 | 영향 |
|---|------|------|
| Q1 | bge-m3 endpoint URL/auth 형식 (가정과 다르면 `EmbeddingClient` 수정) | 공통 |
| Q2 | 배치 최대 크기 / rate limit 정확값 | `EMBED_MAX_BATCH` 결정 |
| Q3 | `query_log` 의미 캐시(E4) 의 `BLOB` 저장 vs 별도 `query_embedding` 테이블 | E4 DDL |
| Q4 | E2 `centroid` 모드를 디폴트로 둘지 LR로 둘지 | Golden Eval 결과로 결정 |
| Q5 | 본 문서 항목들을 별도 PR 시리즈로 vs 한 PR에 묶을지 | 리뷰 비용 |

답 확정 후 본 문서를 v1.1로 갱신, `writing-plans` 스킬로 Phase A 구현 플랜 작성 진행.
