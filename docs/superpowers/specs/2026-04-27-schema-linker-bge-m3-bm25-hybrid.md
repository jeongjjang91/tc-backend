# SchemaLinker BGE-M3 + BM25 하이브리드 전환 가이드

> 작성일: 2026-04-27
> 상태: 설계 (구현 가이드)
> 관련 문서:
> - `2026-04-27-bge-m3-embedding-application-design.md` — E1 (schema_store 의미 검색), EmbeddingClient 공통 인프라
> - `2026-04-21-text-to-sql-improvement-and-eval-system.md` — T5 (description 품질), T11 (Schema Linker 2단계 재랭킹)

---

## 0-A. 검토 반영 보완안

이 문서의 구현 기준은 아래 보완안을 우선한다. 아래 내용과 이후 섹션의 예시 코드가 충돌하면 이 섹션을 따른다.

### A1. `SchemaStore.search()`는 async 전환을 명시적으로 전파한다

BGE-M3 query embedding 호출이 필요하므로 `SchemaStore.search()`는 `async def`가 된다.
따라서 기존 호출부는 모두 `await`로 바꾼다.

수정 대상:

- `app/core/agents/db/schema_linker.py`
- `app/core/agents/db/agent.py`
- `tests/unit/test_schema_store.py`
- `tests/integration/test_db_agent_flow.py`

변경 예:

```python
# app/core/agents/db/schema_linker.py
async def link(self, question: str) -> dict:
    results = await self.schema_store.search(question, top_k=self.top_k)
    schema_context = self.schema_store.format_for_prompt(results)
    prompt = self.renderer.render(
        "schema_linker",
        schema_context=schema_context,
        question=question,
    )
    return await self.llm.complete_json(prompt)
```

```python
# app/core/agents/db/agent.py
linked = await self.linker.link(question)
results = await self.schema_store.search(question, top_k=5)
schema_subset = self.schema_store.format_for_prompt(
    [r for r in results if r["table"] in linked.get("tables", [])] or results[:3]
)
```

테스트도 `pytest.mark.asyncio`를 붙이고 `await store.search(...)` 형태로 변경한다.

### A2. BGE-M3 장애 fallback은 search뿐 아니라 load 시점에도 적용한다

기동 중 embedding API가 죽어 있으면 앱이 시작하지 못하는 문제가 생긴다.
따라서 `load()`에서 schema embedding 생성 실패 시 `_matrix = None`으로 두고 BM25-only 모드로 시작한다.

권장 구현:

```python
async def load(self, schema: dict) -> None:
    self._schema = schema
    tables = schema.get("tables", {})
    self._table_names = list(tables.keys())
    self._docs = [self._table_to_doc(name, tconf) for name, tconf in tables.items()]
    self._tokenized = [tokenize(doc) for doc in self._docs]
    self._bm25 = BM25Okapi(self._tokenized, k1=self._bm25_k1, b=self._bm25_b)

    try:
        self._matrix = await self._embedder.encode_batch_cached(self._docs, scope="schema")
        self._dense_available = True
    except Exception as exc:
        logger.warning("schema_dense_index_unavailable", error=str(exc))
        self._matrix = None
        self._dense_available = False
```

`search()`는 `_matrix is None`이면 BM25-only ranking을 반환한다.

### A3. BM25 토크나이저는 공백 split 대신 식별자 보존 정규식을 사용한다

`doc.lower().split()`는 `TC_EQP_PARAM에서`, `EQP_ID기준`, `ERR_0x4A,` 같은 입력을 제대로 매칭하지 못한다.
BM25는 정확 식별자 매칭을 담당하므로 토큰화가 중요하다.

권장 토크나이저:

```python
import re

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|[0-9]+|[가-힣]+")

def tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in TOKEN_RE.finditer(text)]
```

문서와 query 모두 같은 `tokenize()`를 사용한다.

```python
self._tokenized = [tokenize(doc) for doc in self._docs]
bm25_scores = np.array(self._bm25.get_scores(tokenize(query)))
```

### A4. RRF는 weighted RRF와 exact identifier boost를 함께 적용한다

단순 RRF는 dense rank와 BM25 rank를 동일 가중치로 합산한다.
이 경우 정확 테이블명 또는 컬럼명이 query에 들어와도 dense ranking이 반대로 작동하면 BM25 강점이 약해질 수 있다.

권장 정책:

- 기본은 weighted RRF
- BM25 exact identifier match가 있으면 소폭 boost
- `dense_weight`, `bm25_weight`, `exact_boost`를 설정으로 관리

예:

```python
rrf = np.zeros(n, dtype=np.float64)

for rank, idx in enumerate(dense_ranks):
    rrf[idx] += self._dense_weight / (self._rrf_k + rank + 1)

for rank, idx in enumerate(bm25_ranks):
    rrf[idx] += self._bm25_weight / (self._rrf_k + rank + 1)

query_tokens = set(tokenize(query))
for idx, table in enumerate(self._table_names):
    doc_tokens = set(self._tokenized[idx])
    identifiers = {table.lower()}
    identifiers.update(
        col.lower()
        for col in self._schema["tables"][table].get("columns", {}).keys()
    )
    if query_tokens & identifiers:
        rrf[idx] += self._exact_boost
```

권장 기본값:

```yaml
rrf_k: 60
dense_weight: 1.0
bm25_weight: 1.3
exact_boost: 0.05
```

### A5. 현재 repo 구조에 맞춰 `init_dependencies()`에서 주입한다

현재 프로젝트는 FastAPI lifespan 예제가 아니라 `app/api/deps.py`의 `init_dependencies()`에서 의존성을 조립한다.
따라서 아래 방식으로 반영한다.

```python
from pathlib import Path
from app.infra.llm.embedding_client import EmbeddingClient

embedder = EmbeddingClient(
    base_url=s.embed_api_base_url,
    api_key=s.embed_api_key,
    model=s.embed_model,
    cache_dir=Path(s.embed_cache_dir) if s.embed_cache_dir else None,
    max_batch=s.embed_max_batch,
)

schema_store = SchemaStore(
    embedder=embedder,
    rrf_k=schema_linker_cfg.get("rrf_k", 60),
    bm25_k1=schema_linker_cfg.get("bm25_k1", 1.5),
    bm25_b=schema_linker_cfg.get("bm25_b", 0.75),
    dense_weight=schema_linker_cfg.get("dense_weight", 1.0),
    bm25_weight=schema_linker_cfg.get("bm25_weight", 1.3),
    exact_boost=schema_linker_cfg.get("exact_boost", 0.05),
    fallback_to_bm25_only=schema_linker_cfg.get("fallback_to_bm25_only", True),
)
await schema_store.load(schema_data)
```

`app/config.py`에는 다음 설정을 추가한다.

```python
embed_api_base_url: str = "http://localhost:8081/v1"
embed_api_key: str = ""
embed_model: str = "bge-m3"
embed_cache_dir: str = ".cache/embeddings"
embed_max_batch: int = 256
```

### A6. 설정 파일은 `config/schema_linker.yaml`로 추가하고 loader를 확장한다

신규 설정:

```yaml
top_k: 5

rrf_k: 60
dense_weight: 1.0
bm25_weight: 1.3
exact_boost: 0.05

bm25_k1: 1.5
bm25_b: 0.75

fallback_to_bm25_only: true
```

`app/infra/config/loader.py`에는 `load_schema_linker()`를 추가한다.

```python
def load_schema_linker(self) -> dict:
    return self._load("schema_linker.yaml")
```

### A7. 의존성 추가 위치는 `pyproject.toml`이다

이 repo는 `requirements.txt`가 아니라 `pyproject.toml` 기반이다.
`rank-bm25` 패키지를 사용할 경우 `pyproject.toml` dependencies에 추가한다.

```toml
"rank-bm25>=0.2.2",
```

의존성 추가를 피하려면 본 문서의 직접 구현 `app/infra/db/bm25.py`를 사용한다.

### A8. 테스트는 deterministic vector로 작성한다

`np.random.rand()`를 사용하면 dense rank가 매번 달라질 수 있다.
테스트에서는 고정 벡터를 사용한다.

예:

```python
embedder.encode_batch_cached.return_value = np.array(
    [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ],
    dtype=np.float32,
)
embedder.encode.return_value = np.array([0.0, 1.0, 0.0], dtype=np.float32)
```

필수 테스트:

- `load()`가 BM25와 dense matrix를 만든다.
- `load()` 중 embedding 실패 시 BM25-only로 시작한다.
- `search()` 중 query embedding 실패 시 BM25-only 결과를 반환한다.
- 정확 table identifier query는 해당 테이블이 1위다.
- 의미 query는 dense ranking이 반영된다.
- 기존 `format_for_prompt()` 출력 형식은 유지된다.

---

## 0. 요약

현재 `SchemaStore`는 TF-IDF(`char_wb`, ngram 2~4)만 사용한다. 이를 **BGE-M3(dense) + BM25(sparse) 하이브리드**로 교체하면:

- BGE-M3 → 한국어·혼용 표현·의미 동치 매칭
- BM25 → 설비 코드(`TC_EQP_PARAM`, `ERR_0x4A` 등) 정확 식별자 매칭

두 점수를 **RRF(Reciprocal Rank Fusion)** 로 합산해 최종 top-k를 반환한다. 호출부(`SchemaLinker.link()`)는 시그니처 변경 없이 동작한다.

---

## 1. 왜 TF-IDF → 단독 BGE-M3가 아니라 하이브리드인가

| 시나리오 | TF-IDF | BGE-M3 단독 | BGE-M3 + BM25 |
|---------|--------|------------|--------------|
| "설비 가동률" ↔ `UTILIZATION_RATE` | 표면 미스 | 의미 매칭 | 의미 매칭 |
| `TC_EQP_PARAM` 정확 코드 검색 | 강함 | 벡터 공간에서 거리 클 수 있음 | BM25가 정확 보완 |
| 한영 혼용 (`라인 utilization`) | 형태소 분리 필요 | 기본 지원 | 기본 지원 |
| API 장애 fallback | — | TF-IDF로만 | BM25로만 (오프라인 동작) |

설비명·컬럼명은 **고유 식별자**이므로 의미 벡터보다 키워드 매칭이 더 신뢰할 수 있다. 하이브리드는 두 약점을 상호 보완한다.

---

## 2. 핵심 개념

### 2-1. BM25

TF-IDF 개선판. 단어 포화(saturation)와 문서 길이를 보정한다.

```
Score(q, d) = Σ IDF(t) × tf(t,d)×(k1+1) / [tf(t,d) + k1×(1 - b + b×|d|/avgdl)]
```

- `k1 = 1.5` (포화 조절), `b = 0.75` (문서 길이 정규화) — `rank_bm25` 라이브러리 기본값 사용
- 설치: `pip install rank-bm25`

### 2-2. RRF (Reciprocal Rank Fusion)

두 랭킹을 점수 정규화 없이 합산하는 방법. 점수 스케일이 달라도 안정적이다.

```
RRF_score(d) = Σ_r  1 / (k + rank_r(d))
```

- `k = 60` (표준 기본값, 하위 랭크 문서의 기여를 완화)
- BGE-M3 랭킹 + BM25 랭킹 → 각 문서 RRF 합산 → 정렬

---

## 3. BM25 구현 선택

### 옵션 A — `rank-bm25` 패키지 (기본)

```
pip install rank-bm25   # 순수 Python, 외부 의존성 없음, ~10 KB
```

`BM25Okapi(tokenized, k1=1.5, b=0.75)` 로 바로 사용.

### 옵션 B — 직접 구현 (의존성 추가 없이)

`scikit-learn`이 이미 있으므로 새 패키지 없이 아래 파일 하나로 대체 가능.

```python
# app/infra/db/bm25.py

from __future__ import annotations
import math
from collections import Counter


class BM25:
    """BM25Okapi 직접 구현. rank-bm25 패키지와 동일 수식."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.n = len(corpus)
        self.avgdl = sum(len(d) for d in corpus) / max(self.n, 1)
        self.df: Counter = Counter(t for doc in corpus for t in set(doc))
        self.corpus = corpus

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log((self.n - df + 0.5) / (df + 0.5) + 1)

    def get_scores(self, query: list[str]) -> list[float]:
        scores = []
        for doc in self.corpus:
            tf = Counter(doc)
            dl = len(doc)
            score = sum(
                self._idf(t) * tf[t] * (self.k1 + 1)
                / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                for t in query
                if t in tf
            )
            scores.append(score)
        return scores
```

`SchemaStore.__init__`에서 `from rank_bm25 import BM25Okapi` 대신 `from app.infra.db.bm25 import BM25` 로 교체하면 나머지 코드는 동일하다 (`get_scores` 인터페이스가 같음).

### 선택 기준

| 기준 | 옵션 A (패키지) | 옵션 B (직접 구현) |
|-----|--------------|-----------------|
| 추가 의존성 | `rank-bm25` 1개 | 없음 |
| 유지보수 | 외부 관리 | 내부 관리 |
| 성능 | 동일 (순수 Python) | 동일 |
| 팀 정책이 "의존성 최소화"라면 | — | 권장 |

---

## 4. 변경 대상 파일 목록

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `app/infra/db/schema_store.py` | 수정 | TF-IDF → BGE-M3 + BM25 하이브리드 인덱스 |
| `app/infra/db/bm25.py` | 신규 (옵션 B) | 패키지 없이 직접 구현 시 추가 |
| `app/core/agents/db/schema_linker.py` | 무변경 | 시그니처 유지 |
| `app/api/deps.py` | 수정 | `EmbeddingClient` 주입 추가 |
| `config/schema_linker.yaml` | 신규 | `top_k`, `rrf_k`, `bm25_k1`, `bm25_b` 파라미터 |
| `tests/unit/test_schema_store_hybrid.py` | 신규 | 단위 테스트 |
| `tests/golden/schema_linking_cases.yaml` | 수정 | 고유 코드 매칭 케이스 추가 |

> **선행 조건:** `app/infra/llm/embedding_client.py` (`EmbeddingClient`)가 `2026-04-27-bge-m3-embedding-application-design.md` §2-2 기준으로 구현되어 있어야 한다.

---

## 5. `schema_store.py` 구현

```python
# app/infra/db/schema_store.py

from __future__ import annotations
import numpy as np
from rank_bm25 import BM25Okapi
from app.infra.llm.embedding_client import EmbeddingClient


class SchemaStore:
    def __init__(self, embedder: EmbeddingClient, rrf_k: int = 60):
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._schema: dict = {}
        self._table_names: list[str] = []
        self._docs: list[str] = []          # 원문 (BM25용)
        self._tokenized: list[list[str]] = []  # 형태소 토큰 (BM25용)
        self._bm25: BM25Okapi | None = None
        self._matrix: np.ndarray | None = None  # (N, 1024) BGE-M3 dense

    # ── 인덱싱 ──────────────────────────────────────────────

    async def load(self, schema: dict) -> None:
        self._schema = schema
        tables = schema.get("tables", {})
        self._table_names = list(tables.keys())

        self._docs = []
        for name, tconf in tables.items():
            col_texts = " ".join(
                f"{cn} {cd.get('description', '')} {cd.get('glossary_hint', '')}"
                for cn, cd in tconf.get("columns", {}).items()
            )
            self._docs.append(f"{name} {tconf.get('description', '')} {col_texts}")

        # BM25: 공백 분리 토크나이저 (식별자 보존이 중요하므로 char-split 하지 않음)
        self._tokenized = [doc.lower().split() for doc in self._docs]
        self._bm25 = BM25Okapi(self._tokenized)

        # BGE-M3: 디스크 캐시 활용 — 동일 스키마면 재호출 0
        self._matrix = await self._embedder.encode_batch_cached(self._docs, scope="schema")

    # ── 검색 ─────────────────────────────────────────────────

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        if self._bm25 is None or self._matrix is None:
            return []

        n = len(self._table_names)

        # 1) BGE-M3 dense 점수 → 랭킹
        q_emb = await self._embedder.encode(query)
        q_n = q_emb / (np.linalg.norm(q_emb) + 1e-8)
        m_n = self._matrix / (np.linalg.norm(self._matrix, axis=1, keepdims=True) + 1e-8)
        dense_scores = m_n @ q_n                      # (N,)
        dense_ranks = np.argsort(-dense_scores)        # 내림차순 인덱스

        # 2) BM25 점수 → 랭킹
        bm25_scores = np.array(self._bm25.get_scores(query.lower().split()))
        bm25_ranks = np.argsort(-bm25_scores)

        # 3) RRF 합산
        rrf = np.zeros(n, dtype=np.float64)
        for rank, idx in enumerate(dense_ranks):
            rrf[idx] += 1.0 / (self._rrf_k + rank + 1)
        for rank, idx in enumerate(bm25_ranks):
            rrf[idx] += 1.0 / (self._rrf_k + rank + 1)

        top_idx = np.argsort(-rrf)[:top_k]

        tables = self._schema.get("tables", {})
        return [
            {
                "table": self._table_names[i],
                "score": float(rrf[i]),
                "dense_score": float(dense_scores[i]),
                "bm25_score": float(bm25_scores[i]),
                "config": tables[self._table_names[i]],
            }
            for i in top_idx
        ]

    # ── 프롬프트 포맷 (기존 유지) ──────────────────────────

    def format_for_prompt(self, results: list[dict]) -> str:
        lines = []
        for r in results:
            name = r["table"]
            conf = r["config"]
            lines.append(f"테이블: {name} — {conf.get('description', '')}")
            for col, cconf in conf.get("columns", {}).items():
                hint = cconf.get("glossary_hint", "")
                lines.append(
                    f"  - {col} ({cconf.get('type','')}) : {cconf.get('description','')} {hint}".strip()
                )
            for rel in conf.get("relationships", []):
                lines.append(f"  관계: {rel}")
        return "\n".join(lines)
```

### 설계 메모

- **`load()`가 `async`로 변경됨.** 기동 시 호출부(`deps.py` lifespan)에서 `await` 필요.
- `search()` 시그니처(`query: str, top_k: int`) 유지 → `SchemaLinker.link()` 무변경.
- 반환 dict에 `dense_score` / `bm25_score` 추가 — 디버깅·ablation 용. 프롬프트 포맷에는 노출 안 함.

---

## 6. `deps.py` 변경 (주입)

```python
# app/api/deps.py (관련 부분만 발췌)

from app.infra.llm.embedding_client import EmbeddingClient
from app.infra.db.schema_store import SchemaStore

@asynccontextmanager
async def lifespan(app: FastAPI):
    embedder = EmbeddingClient(
        base_url=settings.EMBED_API_BASE_URL,
        api_key=settings.EMBED_API_KEY,
        cache_dir=Path(settings.EMBED_CACHE_DIR),
    )
    schema_store = SchemaStore(embedder=embedder)
    await schema_store.load(load_schema_yaml(settings.SCHEMA_YAML_PATH))
    app.state.schema_store = schema_store
    yield
```

---

## 7. 설정 파일 (`config/schema_linker.yaml` 신규)

```yaml
# config/schema_linker.yaml

top_k: 5

# RRF 파라미터
rrf_k: 60          # 표준값. 낮출수록 상위 랭크 집중, 높일수록 균등 혼합

# BM25 파라미터 (rank-bm25 BM25Okapi 기본값)
bm25_k1: 1.5       # 단어 포화 조절 (1.2~2.0 권장)
bm25_b: 0.75       # 문서 길이 정규화 (0=정규화 없음, 1=완전 정규화)

# BGE-M3 장애 시 fallback
fallback_to_bm25_only: true
```

> `bm25_k1`, `bm25_b`는 `BM25Okapi(tokenized, k1=..., b=...)` 생성자에 전달. `SchemaStore.__init__`에 파라미터로 주입하고 `config/schema_linker.yaml`에서 로드.

---

## 8. 장애 대응 — BGE-M3 API 장애 시 BM25 fallback

```python
async def search(self, query: str, top_k: int = 5) -> list[dict]:
    if self._bm25 is None:
        return []

    # BGE-M3 호출 시도
    try:
        q_emb = await self._embedder.encode(query)
        use_dense = True
    except LLMError:
        use_dense = False

    if not use_dense:
        # BM25 단독 모드
        bm25_scores = np.array(self._bm25.get_scores(query.lower().split()))
        top_idx = np.argsort(-bm25_scores)[:top_k]
        tables = self._schema.get("tables", {})
        return [
            {"table": self._table_names[i], "score": float(bm25_scores[i]), "config": tables[self._table_names[i]]}
            for i in top_idx
        ]

    # 정상 경로: RRF ...
```

기존 TF-IDF 인덱스는 제거해도 된다. BM25가 오프라인 fallback을 담당한다.

---

## 9. 단위 테스트 (`tests/unit/test_schema_store_hybrid.py`)

```python
import numpy as np
import pytest
from unittest.mock import AsyncMock
from app.infra.db.schema_store import SchemaStore

SAMPLE_SCHEMA = {
    "tables": {
        "TC_EQP_PARAM": {
            "description": "설비 파라미터 기준값 테이블",
            "columns": {
                "EQP_ID": {"type": "VARCHAR", "description": "설비 ID"},
                "PARAM_NM": {"type": "VARCHAR", "description": "파라미터 명"},
                "PARAM_VAL": {"type": "FLOAT", "description": "파라미터 값"},
            },
            "relationships": [],
        },
        "TC_LINE_STATUS": {
            "description": "라인 가동 상태 이력",
            "columns": {
                "LINE_ID": {"type": "VARCHAR", "description": "라인 ID"},
                "UTILIZATION_RATE": {"type": "FLOAT", "description": "가동률"},
            },
            "relationships": [],
        },
    }
}


@pytest.fixture
def mock_embedder():
    embedder = AsyncMock()
    # TC_EQP_PARAM doc → dim 1024, TC_LINE_STATUS → dim 1024
    embedder.encode_batch_cached.return_value = np.random.rand(2, 1024).astype(np.float32)
    embedder.encode.return_value = np.random.rand(1024).astype(np.float32)
    return embedder


@pytest.mark.asyncio
async def test_load_builds_bm25_and_matrix(mock_embedder):
    store = SchemaStore(embedder=mock_embedder)
    await store.load(SAMPLE_SCHEMA)
    assert store._bm25 is not None
    assert store._matrix.shape == (2, 1024)


@pytest.mark.asyncio
async def test_search_returns_top_k(mock_embedder):
    store = SchemaStore(embedder=mock_embedder)
    await store.load(SAMPLE_SCHEMA)
    results = await store.search("설비 파라미터", top_k=2)
    assert len(results) == 2
    assert all("table" in r for r in results)


@pytest.mark.asyncio
async def test_search_keyword_exact_match_boosted(mock_embedder):
    """TC_EQP_PARAM 정확 식별자 포함 쿼리 시 BM25가 해당 테이블 점수를 올린다."""
    # BGE-M3는 동일 랜덤 임베딩 → dense 기여는 균등
    store = SchemaStore(embedder=mock_embedder)
    await store.load(SAMPLE_SCHEMA)
    results = await store.search("TC_EQP_PARAM 파라미터 조회", top_k=2)
    # BM25는 TC_EQP_PARAM을 토큰으로 직접 매칭 → 1위여야 함
    assert results[0]["table"] == "TC_EQP_PARAM"


@pytest.mark.asyncio
async def test_fallback_bm25_on_embed_error(mock_embedder):
    from app.shared.exceptions import LLMError
    mock_embedder.encode.side_effect = LLMError("api down")
    store = SchemaStore(embedder=mock_embedder)
    await store.load(SAMPLE_SCHEMA)
    results = await store.search("설비 가동률", top_k=2)
    assert len(results) > 0  # BM25 fallback으로 결과 반환
```

---

## 10. Golden Eval 케이스 추가 (`tests/golden/schema_linking_cases.yaml`)

기존 케이스에 다음 유형을 추가한다.

```yaml
# --- BGE-M3 + BM25 하이브리드 검증 케이스 ---

- id: SL_HYBRID_001
  description: "고유 식별자 포함 — BM25 강점"
  question: "TC_EQP_PARAM 테이블에서 EQP_ID 기준으로 조회"
  expected_tables:
    - TC_EQP_PARAM
  must_include_rank_1: true
  tags: [bm25, exact_match]

- id: SL_HYBRID_002
  description: "의미 동치 — BGE-M3 강점"
  question: "라인 utilization 확인하고 싶어"
  expected_tables:
    - TC_LINE_STATUS
  must_include_rank_1: true
  tags: [semantic, mixed_language]

- id: SL_HYBRID_003
  description: "혼합 — 식별자 + 의미"
  question: "TC_EQP_PARAM의 가동률 관련 파라미터"
  expected_tables:
    - TC_EQP_PARAM
  must_include_rank_3: true
  tags: [hybrid]
```

평가 지표: **Schema Linking Recall@5** (기존 TF-IDF baseline 대비 -5% 이상 하락 금지).

---

## 11. 구현 순서 (체크리스트)

```
[ ] 1. `EmbeddingClient` 구현 확인 (`app/infra/llm/embedding_client.py`)
[ ] 2. BM25 구현 선택
        - 옵션 A: `rank-bm25`를 `pyproject.toml` dependencies에 추가
        - 옵션 B: `app/infra/db/bm25.py` 직접 구현 추가
[ ] 3. `config/schema_linker.yaml` 신규 생성
        - rrf_k, dense_weight, bm25_weight, exact_boost, bm25_k1, bm25_b, fallback_to_bm25_only
[ ] 4. `app/config.py`에 embedding 설정 추가
        - embed_api_base_url, embed_api_key, embed_model, embed_cache_dir, embed_max_batch
[ ] 5. `app/infra/config/loader.py`에 `load_schema_linker()` 추가
[ ] 6. `app/infra/db/schema_store.py` 교체
        - async load/search
        - startup BM25-only fallback
        - search-time BM25-only fallback
        - identifier-preserving tokenizer
        - weighted RRF + exact identifier boost
[ ] 7. async 전환 호출부 수정
        - `SchemaLinker.link()`에서 `await schema_store.search(...)`
        - `DBAgent.run()`에서 두 번째 `await schema_store.search(...)`
[ ] 8. `app/api/deps.py` 수정
        - `EmbeddingClient` 생성
        - `SchemaStore(embedder=...)` 생성
        - `await schema_store.load(schema_data)`
[ ] 9. 기존 SchemaStore 테스트 async로 수정
[ ] 10. `tests/unit/test_schema_store_hybrid.py` 신규 작성
        - deterministic vector 사용
        - load fallback, search fallback, exact identifier, semantic ranking 검증
[ ] 11. `tests/integration/test_db_agent_flow.py` async search 변경 반영
[ ] 12. Golden Eval 케이스 추가
[ ] 13. Golden Eval 실행 → Schema Linking Recall@5 baseline 이상 확인
[ ] 14. PR 제출
```

---

## 12. 관련 설계 문서와의 관계

| 본 문서 | 관련 문서 | 관계 |
|--------|---------|------|
| `schema_store.py` BGE-M3 + BM25 | E1 (`bge-m3-embedding-application-design.md`) | E1의 "bge-m3 단독" → 본 문서에서 BM25 하이브리드로 확장 |
| `schema_linker.yaml` 파라미터 | T11 (`text-to-sql-improvement-and-eval-system.md`) | T11 stage-1 구현체의 파라미터 설정 파일 |
| Golden Eval 케이스 | T5 (description 품질) | description 품질이 BGE-M3 정확도에 직결 — T5 작업과 병행 권장 |

---

## 13. Out of Scope

- 컬럼 레벨 BM25 (현재는 테이블 단위 doc 검색 — 컬럼까지 분리하면 T11 2단계 LLM 재랭킹과 역할 중복)
- ANN(FAISS) 도입 — 현재 테이블 수(~수백)에서 전혀 불필요
- Cross-encoder reranker — T11 2단계 LLM 재랭킹이 이 역할을 담당
- 한국어 형태소 분석기(konlpy) 연동 — BM25 토크나이저로 공백 분리만 해도 식별자 보존 측면에서 충분; 정밀도 개선 필요 시 별도 검토
