# TC VOC Chatbot — 설계 문서

- **작성일:** 2026-04-18
- **상태:** Draft (사용자 검토 대기)
- **범위:** 백엔드 시스템 설계 (Phase 1 상세, Phase 2~4 인터페이스 수준)

---

## 1. 배경 및 목표

### 1.1 문제

TC 시스템 운영팀은 현업 기술팀으로부터 다음과 같은 VOC를 지속적으로 받고 있음.

| # | VOC 유형 | 현재 처리 방법 |
|---|----------|--------------|
| 1 | "A 설비에 X 기능이 있나요?" | TC DB(Oracle) 수동 조회 |
| 2 | "A와 B 설비의 기능이 동일한가요?" | TC DB 수동 조회 + 비교 |
| 3 | "C 설비가 동작하지 않는데 원인은?" | Splunk 로그 조회 + 개발자 경험 기반 진단 |
| 4 | "D 기능에 대해 설명해 주세요" | Confluence 등 문서 검색 |

VOC 양은 지속 증가하나 응대 인력은 한정적. 응답 지연이 빈번.

### 1.2 목표

1. 위 4개 유형의 VOC에 대해 **자동 또는 반자동으로 응답**하는 챗봇 백엔드를 구축한다.
2. 사내 오픈소스 LLM(GPT-OSS, Gemma4)을 활용한다. **파인튜닝 불가** 환경에서 **시스템 설계로 응답 품질을 최대화**한다.
3. 시간이 갈수록 답변 품질이 자동 개선되는 **학습 루프**를 갖는다.

### 1.3 비목표 (Out of Scope)

- TC 시스템 자체의 변경
- LLM 모델 자체의 학습/파인튜닝
- 프론트엔드 구현 (별도 진행)
- VOC 채널(이메일/티켓 시스템) 통합 — 챗봇 UI 우선

---

## 2. 제약 조건

| 제약 | 내용 |
|------|------|
| LLM | 사내 제공 오픈소스 LLM API. 파인튜닝 불가. |
| 인프라 | Oracle 1개만 사용. **Redis/Kafka/별도 캐시 서버 사용 금지.** |
| DB 접근 | TC DB는 read-only 커넥션만 |
| 보안 | SELECT만 허용. 화이트리스트 테이블/컬럼만 접근. |
| 외부 의존성 | 사내 LLM API, 사내 RAG API, Splunk API (운영은 외부 팀) |

---

## 3. 자동화 정책

| VOC 유형 | 자동화 수준 | 이유 |
|---------|----------|------|
| 1, 2 (DB 조회/비교) | **완전 자동** | 출력이 데이터 기반, 검증 가능 |
| 4 (기능 설명) | **완전 자동** | 인용 강제로 환각 차단 |
| 3 (오동작 원인) | **반자동 (검토자 승인)** | 도메인 추론 위험, 실수 영향 큼 |

**Confidence가 임계값 미만**이면 유형 1, 2, 4도 검토 큐로 라우팅. 검토자 승인 후 발송 + Knowledge Agent 자동 적재.

---

## 4. 전체 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│ Vue.js Chatbot (SSE: plan/progress/token/citation/confidence)    │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│ FastAPI Gateway                                                  │
│ • Session Memory (Oracle)  • Auth  • Rate Limit  • Tracing       │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│ Orchestrator (Plan-and-Execute)                                  │
│ • Adaptive Routing (No-RAG/Single/Multi-step)                    │
│ • Sub-task Decomposition (JSON 출력)                             │
│ • Parallel/Sequential Execution                                  │
└──┬──────────────┬──────────────┬─────────────────┬───────────────┘
   │              │              │                 │
┌──▼──────────┐ ┌─▼──────────┐ ┌─▼──────────┐ ┌──▼──────────────┐
│ DB Agent    │ │ Log Agent  │ │ Doc Agent  │ │ Knowledge Agent │
│ (Phase 1)   │ │ (Phase 3)  │ │ (Phase 2)  │ │ (Phase 4)       │
└──┬──────────┘ └─┬──────────┘ └─┬──────────┘ └──┬──────────────┘
   │              │              │                │
   ▼              ▼              ▼                ▼
┌──────────────────────────────────────────────────────────────────┐
│ Synthesizer                                                      │
│ • Evidence Reordering (Lost-in-the-middle 회피)                  │
│ • Citation Enforcement (JSON Schema)                             │
│ • Confidence Calibration                                         │
└────────────────────────────┬─────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │ Confidence Router           │
              ├─ High → Auto Send           │
              └─ Low/Type 3 → Review Queue  │
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│ Feedback Loop                                                    │
│ • 👍/👎 Quality Log                                              │
│ • Golden Dataset Regression (CI)                                 │
│ • Reviewed Answer → Knowledge Agent (자동 적재)                  │
│ • Successful (Q,SQL) → Few-shot Bank (자동 적재)                 │
└──────────────────────────────────────────────────────────────────┘
```

### 4.1 핵심 인터페이스 (Phase 1에 미리 잡음)

```python
# core/agents/base.py
class Agent(ABC):
    name: str
    
    @abstractmethod
    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult: ...

# core/orchestrator/plan.py
class SubQuery(BaseModel):
    id: str
    agent: str           # AGENT_REGISTRY 키
    query: str
    depends_on: list[str] = []

class AgentResult(BaseModel):
    sub_query_id: str
    success: bool
    evidence: list[Evidence]    # SQL row / 로그 라인 / 문서 청크
    raw_data: Any               # 원본 (디버깅용)
    confidence: float
    error: Optional[str]

class Evidence(BaseModel):
    id: str                     # 인용 ID
    source_type: Literal["db_row", "log_line", "doc_chunk", "knowledge_entry"]
    content: str
    metadata: dict              # 표 이름, 로그 timestamp, 문서 URL 등

# infra/llm/base.py
class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, prompt: str, **kwargs) -> str: ...
    @abstractmethod
    async def stream(self, prompt: str, **kwargs) -> AsyncIterator[str]: ...
    @abstractmethod
    async def complete_json(self, prompt: str, schema: dict, **kwargs) -> dict: ...
```

이 인터페이스들은 **Phase 1에 확정** — 이후 Agent 추가 시 리팩토링 없음.

---

## 5. Phase 1 상세 설계: DB Agent (Text-to-SQL)

### 5.1 파이프라인

```
질문 → [1] Schema RAG       (관련 테이블/컬럼 Top-K 검색)
     → [2] Value Retrieval  (DB Distinct Values 매칭)
     → [3] Few-shot Retrieval (Skeleton 유사도)
     → [4] Schema Linking   (LLM, JSON 출력)
     → [5] SQL Generation   (LLM + CoT + Few-shot)
     → [6] Static Validation (sqlglot + 화이트리스트)
     → [7] Execution        (read-only, timeout 5s, row 1000)
     → [8] Refiner          (에러 타입별 재생성, max 2회)
     → [9] Result Interpretation (LLM, 자연어 답변 + 인용)
     → [10] Success Cache   (Few-shot Bank 자동 적재)
```

### 5.2 Schema RAG (단계 1)

**문제:** 모든 테이블 스키마를 프롬프트에 넣으면 노이즈 + 토큰 한계.

**해결:**
- `config/schema/tc_oracle.yaml`을 시작 시 인스턴스 in-memory에 로드.
- 각 테이블/컬럼 설명을 임베딩하여 in-memory 벡터 인덱스 구성 (FAISS / numpy).
- 질문 임베딩 → cosine top-K (기본 K=5 테이블).
- **임베딩 모델 결정:** Phase 1 시작 시 사내 LLM API에 임베딩 엔드포인트가 있는지 확인 후 결정. 없으면 사내 RAG API 또는 로컬 sentence-transformers(`bge-m3` 등) 사용. (§14 미결 사항)

**YAML 예시:**

```yaml
tables:
  PARAMETER:
    description: "설비별 파라미터 정의 마스터. 한 설비당 다수 행."
    columns:
      param_id:
        type: NUMBER
        description: "파라미터 고유 ID"
      param_name:
        type: VARCHAR2
        description: "파라미터 명칭. 'PARAM_*' 형식"
      eqp_id:
        type: VARCHAR2
        description: "설비 ID. 'EQP_<영역>_<번호>' 형식"
        glossary_hint: "사용자가 'A 설비'라고 하면 EQP_A_*"
    relationships:
      - "PARAMETER.eqp_id = MODEL_INFO.eqp_id"
```

### 5.3 Value Retrieval (단계 2)

**문제:** 사용자는 "A 설비"라고 하지만 실제 DB엔 'EQP_A_001' 형태로 저장.

**해결:**
- 스키마 YAML에 `searchable: true`로 마킹된 컬럼만 대상.
- 주기적으로 distinct values를 추출하여 in-memory trigram 인덱스 구성.
- **명사구 추출:** Phase 1은 정규식 + 사용자 사전(고유명사 화이트리스트) 기반 단순 추출로 시작. 정확도 부족 시 LLM 1회 호출로 업그레이드.
- trigram 매칭 → 후보 Top-N (기본 N=5/term).
- 후보를 SQL 생성 프롬프트의 컨텍스트에 주입.

**구현 메모:**
- 대용량 컬럼은 캐시 갱신 비용이 큼 → **빈도 높은 컬럼만** (설비 ID, 파라미터명, 모델명).
- 갱신 주기: 일 1회 batch 또는 변경 시 trigger.

### 5.4 Few-shot Retrieval (단계 3, DAIL-SQL)

**문제:** 정적 few-shot 예시는 다양한 질문 패턴에 비효율.

**해결:**
- 질문 → Skeleton 추출.
  - **Phase 1:** 정규식 기반 마스킹 (값 후보로 인식된 토큰을 `<EQP>`, `<PARAM>` 등으로 치환). LLM 호출 없음.
  - 예: `"A 설비에 PARAM_X 있나?"` → `"<EQP>에 <PARAM> 있나?"`
- `few_shot_bank` 테이블에서 Skeleton 유사도 Top-K 검색.
- Phase 1 시작 시점엔 **YAML 시드 30~50개**로 부트스트랩.
- 운영 중 성공한 (질문, SQL) 쌍이 자동 누적.

### 5.5 Schema Linking (단계 4, DIN-SQL)

**문제:** SQL 생성과 스키마 식별을 한 프롬프트에 합치면 정확도 ↓.

**해결:** 별도 LLM 호출로 분리. JSON 출력 강제.

```
입력: 질문 + Schema RAG Top-K 결과
출력: {
  "tables": ["MODEL_INFO", "PARAMETER"],
  "columns": ["MODEL_INFO.model_name", "PARAMETER.param_name", ...],
  "joins": ["MODEL_INFO.eqp_id = PARAMETER.eqp_id"]
}
```

이 결과만 SQL 생성 프롬프트의 스키마 컨텍스트로 사용 → 노이즈 최소화.

### 5.6 SQL Generation (단계 5)

**프롬프트 구조 (Jinja2):**

```jinja
당신은 Oracle SQL 전문가입니다. 단계별로 사고한 뒤 SQL을 생성하세요.

[관련 스키마]
{{ schema_linked_subset }}

[유사 예시 (Skeleton 매칭)]
{% for ex in few_shots %}
질문: {{ ex.question }}
SQL: {{ ex.sql }}
{% endfor %}

[값 후보]
{% for term, candidates in value_candidates.items() %}
- "{{ term }}" → {{ candidates }}
{% endfor %}

[질문]
{{ user_question }}

[추론 단계]
1. 어떤 테이블이 필요한가?
2. 어떤 컬럼이 필요한가?
3. JOIN 조건은?
4. WHERE 조건과 사용할 값은?
5. 결과를 어떻게 제한할 것인가? (ROWNUM)

[출력 — 반드시 JSON]
{
  "reasoning": "<step 1~5 요약>",
  "sql": "<Oracle SQL>",
  "confidence": <0~1>,
  "assumptions": ["<가정 목록>"]
}
```

### 5.7 Static Validation (단계 6)

**핵심 가드레일 (LLM 호출 없음):**

1. `sqlglot.parse_one(sql, dialect="oracle")` — 문법 파싱 실패 시 reject
2. `isinstance(tree, exp.Select)` — SELECT만 허용
3. 사용 테이블 ⊆ 화이트리스트 (`config/whitelist.yaml`)
4. 사용 컬럼 ⊆ 화이트리스트
5. 대용량 테이블 사용 시 WHERE 절 강제
6. ROWNUM 제한 자동 주입 (없으면 1000 row)
7. 위험 함수 차단 (DBMS_*, UTL_*, EXEC IMMEDIATE 등)

**`config/whitelist.yaml` 구조:**

```yaml
tables:
  PARAMETER:
    columns: [param_id, param_name, eqp_id, created_at]
    requires_where_clause: true
  MODEL_INFO:
    columns: [eqp_id, model_name, version, created_at]
    requires_where_clause: false
large_tables: [DCOL_LOG, EVENT_HISTORY]
forbidden_functions: ["DBMS_*", "UTL_*"]
```

### 5.8 Execution (단계 7)

- Read-only 커넥션 풀 (별도 DB 사용자)
- `cx_Oracle` 또는 `oracledb` (async wrapper) 사용
- Statement timeout: 5초
- Row limit: 1000 (애플리케이션 레벨에서 fetchmany)

### 5.9 Refiner (단계 8, MAC-SQL)

**에러 타입별 차별화된 피드백:**

| 에러 타입 | 피드백 프롬프트 | 비고 |
|---------|-------------|------|
| Syntax error | Oracle 에러 메시지 + 원본 SQL 첨부, 재생성 요청 | max 2회 |
| Empty result | "결과 0건. WHERE 조건이 너무 좁을 수 있음" | 조건 완화 가이드 |
| Too many rows | "결과 N건 초과. GROUP BY 필요할 수 있음" | 집계 가이드 |
| Validation fail (화이트리스트) | "허용되지 않은 테이블/컬럼 사용. 다음만 사용 가능: [목록]" | max 1회 |

각 재시도는 LLM 호출 +1. 비용/지연 트레이드오프.

### 5.10 Result Interpretation (단계 9)

```jinja
다음 SQL과 결과를 바탕으로 사용자 질문에 답하세요.

[규칙]
1. 모든 주장에 [row_<n>] 형식의 인용 필수
2. SQL 결과에 없는 내용은 절대 답하지 말 것
3. 결과가 0건이면 "확인되지 않습니다"로 답할 것

[질문] {{ question }}
[SQL] {{ sql }}
[결과] (총 {{ row_count }}건)
{% for row in rows[:20] %}
[row_{{ loop.index }}] {{ row }}
{% endfor %}

[출력 — JSON]
{
  "answer": "...[row_1]...에 따라 ...",
  "confidence": 0.85,
  "needs_human_review": false,
  "missing_info": []
}
```

### 5.11 Success Cache (단계 10)

성공 (Confidence ≥ 임계값 + 사용자 👍 또는 검토 통과) 시:

```sql
INSERT INTO few_shot_bank (
  question_skeleton, question_original, sql_text,
  source, success_rate
) VALUES (?, ?, ?, 'auto', 1.0);

-- 동일 skeleton 중복 시 hit_count 증가
```

다음 유사 질문에 자동으로 활용.

---

## 6. Phase 2~4 인터페이스 수준 설계

### 6.1 Phase 2: Doc Agent (RAG)

**핵심 기법:**
- **Anthropic Contextual Retrieval** (인덱싱 시 청크별 컨텍스트 LLM 자동 생성)
- Hybrid Retrieval: BM25(OpenSearch) + Vector(사내 RAG API) + RRF Fusion
- Cross-encoder Reranking (BGE-Reranker-v2-m3 등)
- Parent-Child Chunking (구조 인식: 헤딩/코드블록/표 보존)
- Adaptive Routing (No-RAG/Single/Multi-step)

**인터페이스:**

```python
class DocAgent(Agent):
    name = "doc"
    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        # 1. (Optional) Step-Back Prompting
        # 2. Hybrid Retrieval → 50개
        # 3. Rerank → 10개
        # 4. Parent Expansion
        # 5. Evidence 패키징
```

### 6.2 Phase 3: Log Agent (Splunk)

**핵심 기법:**
- 2-단계: SPL 생성 + 로그 패턴 분석
- 시간 범위 추출 별도 LLM 호출
- 패턴 라이브러리 (Drain 알고리즘 + 도메인 패턴)
- Confidence < 임계값 → 검토 큐 (기본 정책)

**인터페이스:**

```python
class LogAgent(Agent):
    name = "log"
    async def run(self, sub_query, context) -> AgentResult:
        # 1. Time range extraction
        # 2. SPL generation (Text-to-SQL과 동일 패턴)
        # 3. Splunk execution
        # 4. Log parsing (Drain) + Pattern matching
        # 5. Hypothesis generation (LLM)
```

### 6.3 Phase 4: Knowledge Agent (LLM Wiki)

**구조화 메모리:** `knowledge_entries` 테이블 (스키마는 §7 참조).

**검색 가중치:**
```
score = semantic_similarity 
      * confidence
      * time_decay(reviewed_at)
      * entity_match_bonus
      * (1 if not superseded else 0)
```

**충돌 감지:** 같은 `question_skeleton` + 같은 `entities`인데 결론 다른 답변 → 검토자 알림.

---

## 7. 데이터 모델 (Oracle 테이블)

**참고:** `query_log.result_summary`는 캐시 컨텐츠 겸용. `cached_until > now()`이면 동일 `question_hash` 질문 시 LLM 호출 없이 재사용. TTL은 agent별로 차등 (DB 결과는 짧게, 문서 답변은 길게).

### 7.1 운영 데이터 (Phase 1 필수)

```sql
-- 채팅 세션
CREATE TABLE chat_sessions (
  session_id     VARCHAR2(36) PRIMARY KEY,
  user_id        VARCHAR2(50),
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP,
  last_active_at TIMESTAMP,
  metadata       CLOB CHECK (metadata IS JSON)
);

-- 대화 메시지
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

-- 피드백
CREATE TABLE feedback_log (
  feedback_id    NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  message_id     NUMBER REFERENCES chat_messages(message_id),
  user_id        VARCHAR2(50),
  rating         CHAR(1),         -- 'P'/'N'
  comment        CLOB,
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- 쿼리 로그 (분석 + 캐시 겸용)
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

-- 설정 버전 (폴링 기반 리로드)
CREATE TABLE config_version (
  scope          VARCHAR2(50) PRIMARY KEY,
  version        NUMBER,
  updated_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);
```

### 7.2 Phase 1 추가 테이블

```sql
-- Few-shot Bank (자동 누적)
CREATE TABLE few_shot_bank (
  id                 NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  question_skeleton  CLOB,
  question_original  CLOB,
  sql_text           CLOB,
  source             VARCHAR2(20),    -- 'auto'/'manual'/'seed'
  hit_count          NUMBER DEFAULT 0,
  success_rate       NUMBER(3,2),
  enabled            CHAR(1) DEFAULT 'Y',
  created_at         TIMESTAMP DEFAULT SYSTIMESTAMP
);
```

### 7.3 Phase 3~4 추가 (참고)

```sql
-- 검토 큐 (Phase 3)
CREATE TABLE review_queue (
  review_id      NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  message_id     NUMBER REFERENCES chat_messages(message_id),
  draft_answer   CLOB,
  evidence       CLOB CHECK (evidence IS JSON),
  status         VARCHAR2(20),         -- 'pending'/'reviewing'/'approved'/'rejected'
  assignee       VARCHAR2(50),
  reviewed_by    VARCHAR2(50),
  final_answer   CLOB,
  reviewed_at    TIMESTAMP,
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- Knowledge Entries (Phase 4)
CREATE TABLE knowledge_entries (
  id                 VARCHAR2(36) PRIMARY KEY,
  question_skeleton  CLOB,
  question_original  CLOB,
  answer             CLOB,
  evidence           CLOB CHECK (evidence IS JSON),
  entities           CLOB CHECK (entities IS JSON),
  answer_type        VARCHAR2(20),
  reviewer           VARCHAR2(50),
  reviewed_at        TIMESTAMP,
  confidence         NUMBER(3,2),
  valid_until        TIMESTAMP,
  superseded_by      VARCHAR2(36),
  hit_count          NUMBER DEFAULT 0,
  created_at         TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- 패턴 라이브러리 (Phase 3, YAML 시드 + 운영 추가)
CREATE TABLE pattern_library (
  pattern_id   VARCHAR2(50) PRIMARY KEY,
  source       CHAR(1),                -- 'Y'/'D'
  category     VARCHAR2(30),           -- 'log'/'sql'
  regex        CLOB,
  detector_fn  VARCHAR2(100),
  severity     VARCHAR2(10),
  hint         CLOB,
  enabled      CHAR(1) DEFAULT 'Y',
  updated_by   VARCHAR2(50),
  updated_at   TIMESTAMP
);

-- 런타임 설정 오버라이드
CREATE TABLE config_overrides (
  config_key   VARCHAR2(100) PRIMARY KEY,
  config_value CLOB CHECK (config_value IS JSON),
  enabled      CHAR(1) DEFAULT 'Y',
  updated_by   VARCHAR2(50),
  updated_at   TIMESTAMP DEFAULT SYSTIMESTAMP,
  reason       VARCHAR2(500)
);

-- 감사 로그
CREATE TABLE config_audit_log (
  audit_id     NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  scope        VARCHAR2(50),
  config_key   VARCHAR2(100),
  old_value    CLOB,
  new_value    CLOB,
  changed_by   VARCHAR2(50),
  reason       VARCHAR2(500),
  changed_at   TIMESTAMP DEFAULT SYSTIMESTAMP
);
```

---

## 8. 설정 관리 (3계층)

| 계층 | 변경 빈도 | 권한 | 배포 | 대상 |
|------|---------|------|------|------|
| **Code** | 낮음 | 개발자 + PR | ✅ | Agent 클래스, 핵심 로직 |
| **YAML** (Repo) | 중간 | 개발자/QA + PR | ✅ | 프롬프트, 스키마 설명, 시드 패턴, **화이트리스트(보안 임계)** |
| **DB** (Runtime) | 높음 | 운영자 (UI/API) | ❌ | Few-shot 누적, Knowledge, 임계값 오버라이드, Feature Flag |

### 8.1 YAML 디렉토리 구조

```
config/
├── agents.yaml             # 활성 agent + 기본 옵션
├── prompts/
│   ├── planner.j2
│   ├── schema_linker.j2
│   ├── sql_gen.j2
│   ├── sql_refiner.j2
│   └── synthesizer.j2
├── schema/
│   └── tc_oracle.yaml
├── patterns/
│   └── log_patterns.yaml   # Phase 3
├── few_shot/
│   └── sql_seed.yaml
├── thresholds.yaml
└── whitelist.yaml          # 보안 임계 — DB 변경 금지
```

### 8.2 폴링 기반 리로드

```python
# infra/config/poller.py
class ConfigPoller:
    def __init__(self, interval_sec: int = 30):
        self.versions: dict[str, int] = {}
        self.interval = interval_sec

    async def loop(self):
        while True:
            current = await db.fetch_all("SELECT scope, version FROM config_version")
            for row in current:
                if self.versions.get(row.scope) != row.version:
                    await self._reload(row.scope)
                    self.versions[row.scope] = row.version
            await asyncio.sleep(self.interval)
```

운영자가 DB INSERT/UPDATE → `UPDATE config_version SET version=version+1 WHERE scope='X'` → 30초 내 모든 인스턴스 반영.

---

## 9. FastAPI 백엔드 구조

```
C:/project/backend/
├── app/
│   ├── main.py
│   ├── config.py                    # Pydantic Settings (env vars)
│   ├── api/
│   │   ├── deps.py
│   │   ├── middleware/
│   │   │   ├── auth.py
│   │   │   ├── rate_limit.py
│   │   │   └── tracing.py
│   │   └── v1/
│   │       ├── chat.py              # POST /chat (SSE)
│   │       ├── feedback.py
│   │       ├── review.py            # Phase 3
│   │       └── admin.py
│   ├── core/                        # 도메인 (FastAPI 무관)
│   │   ├── orchestrator/
│   │   │   ├── planner.py
│   │   │   └── executor.py
│   │   ├── agents/
│   │   │   ├── base.py              # Agent ABC
│   │   │   ├── registry.py          # AGENT_REGISTRY
│   │   │   ├── db_agent.py          # Phase 1
│   │   │   ├── doc_agent.py         # Phase 2
│   │   │   ├── log_agent.py         # Phase 3
│   │   │   └── knowledge_agent.py   # Phase 4
│   │   ├── synthesizer.py
│   │   └── confidence.py
│   ├── infra/
│   │   ├── llm/
│   │   │   ├── base.py              # LLMProvider ABC
│   │   │   ├── internal_api.py      # 사내 LLM API
│   │   │   └── prompts/             # Jinja2 렌더러
│   │   ├── db/
│   │   │   ├── oracle.py            # read-only pool
│   │   │   ├── schema_store.py      # Schema RAG (in-memory)
│   │   │   └── value_store.py       # Value Retrieval (in-memory trigram)
│   │   ├── splunk/                  # Phase 3
│   │   ├── rag/                     # Phase 2 (사내 RAG API + Reranker)
│   │   └── config/
│   │       └── poller.py
│   ├── eval/
│   │   ├── golden_runner.py
│   │   ├── metrics.py
│   │   └── reports.py
│   └── shared/
│       ├── schemas.py               # Pydantic 모델
│       ├── exceptions.py
│       └── logging.py               # 구조화 + trace_id
├── config/                          # § 8.1 참조
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── component/
│   └── golden/
│       ├── datasets/
│       │   ├── db_phase1.yaml
│       │   ├── doc_phase2.yaml
│       │   └── log_phase3.yaml
│       └── runner.py
├── docs/superpowers/specs/
├── pyproject.toml
└── docker-compose.yml               # 로컬 Oracle (테스트용)
```

### 9.1 핵심 엔드포인트

```python
# api/v1/chat.py
@router.post("/chat")
async def chat(req: ChatRequest, ...) -> StreamingResponse:
    """SSE 이벤트:
       event: plan       — 실행 계획 알림
       event: progress   — sub-task 진행
       event: token      — 답변 토큰
       event: citation   — 인용 메타
       event: confidence — 최종 confidence
       event: done       — 완료
    """
    return StreamingResponse(
        chat_service.stream(req),
        media_type="text/event-stream",
    )
```

---

## 10. 테스트 전략

### 10.1 5계층 피라미드

```
              E2E 수동 smoke           ← 릴리즈 전
              Golden Eval              ← PR마다 (회귀 -5% 차단)
              Component (실 LLM)      ← PR pre-merge
              Integration (Mock LLM)  ← 매 커밋
              Unit                    ← 매 커밋
```

### 10.2 Golden Dataset

`tests/golden/datasets/db_phase1.yaml` — easy 15 / medium 10 / hard 5 (Phase 1 종료 시 30개 baseline).

```yaml
- id: db_001
  difficulty: easy
  question: "A 설비에 PARAM_X 기능 있어?"
  expected:
    answer_must_contain: ["EQP_A_001", "PARAM_X"]
    sql_pattern: "SELECT.*FROM\\s+PARAMETER.*WHERE.*"
    sql_must_filter_on: ["eqp_id", "param_name"]
    citation_required: true
    expected_result_count: ">= 1"
```

### 10.3 자동 평가 메트릭

| 메트릭 | 계산 |
|--------|------|
| **SQL Execution Match** | 정답 SQL과 생성 SQL 결과의 row Jaccard 유사도 |
| **Answer Recall** | 정답 키워드 매칭률 |
| **Hallucination Rate** | 답변 entity가 SQL 결과에 실존하는가 |
| **Citation Coverage** | 답변 문장 중 인용 비율 |
| **Pass Rate by Difficulty** | easy/medium/hard 각각 |

### 10.4 CI 게이트

```yaml
fast (매 커밋):       unit + integration  (~30s)
pre_merge (PR):      + component (실 LLM) + golden  (~10~20m, 회귀 -5% 차단)
nightly:             golden full + 트렌드 리포트
pre_release:         + 수동 E2E smoke
```

### 10.5 LLM 시스템 테스트 3대 원칙

(`CLAUDE.md`에 명시)

1. 정확한 출력 일치를 검증하지 말 것 — 속성/계약만.
2. 프롬프트 변경은 항상 Golden Eval로 검증.
3. Golden Dataset은 코드와 동급의 자산.

---

## 11. 보안

| 영역 | 정책 |
|------|------|
| DB 접근 | Read-only 커넥션 전용 (별도 DB 사용자) |
| SQL | SELECT만 허용, 화이트리스트(`config/whitelist.yaml`) 외 차단, LIMIT 자동 주입 |
| 위험 함수 | DBMS_*, UTL_*, EXEC IMMEDIATE 등 차단 (`sqlglot` 기반 검사) |
| Prompt Injection | 사용자 입력은 시스템 프롬프트와 명확히 분리. 화이트리스트는 DB로 변경 불가. |
| 인증/자격증명 | LLM API 키, DB 비밀번호는 환경변수/Secret Manager. 코드/설정 파일에 저장 금지. |
| 감사 | 모든 설정 변경은 `config_audit_log`에 기록 (who/when/why) |

---

## 12. 관측성

- **trace_id**: 모든 요청에 UUID 발급. LLM 호출/DB 쿼리/로그 전부 동일 ID로 연결.
- **구조화 로깅**: JSON 라인. `request`/`agent`/`llm_call`/`db_query`/`error` 카테고리.
- **메트릭** (Phase 1):
  - 응답 latency p50/p95/p99
  - LLM 호출 횟수/토큰
  - SQL 검증 실패율
  - Self-correction 트리거 비율
  - 사용자 👍/👎 비율
- **대시보드**: Phase 4에서 운영 대시보드 추가.

---

## 13. Phase 별 완료 기준

| Phase | 완료 기준 |
|-------|---------|
| **1** | DB Agent 단독으로 Type 1, 2 질문 30개 Golden 통과 (easy 90%+ / medium 70%+ / hard 50%+) |
| **2** | Doc Agent + Orchestrator. Type 4 추가. Doc Golden 30개 baseline 등록. |
| **3** | Log Agent + 검토 큐. Type 3 반자동 워크플로우 동작. |
| **4** | Knowledge Agent + Golden 평가 CI 자동화 + 운영 대시보드. |

---

## 14. 미결 사항 (Phase 진행 중 결정)

- [ ] 사내 LLM API 명세 확인 (스트리밍 지원? JSON 모드? 토큰 한계? 임베딩 엔드포인트?)
- [ ] 사내 RAG API 명세 (검색 인터페이스, 메타데이터 지원, 인덱스 직접 적재 가능?)
- [ ] Schema RAG / Skeleton 매칭용 임베딩 모델 (사내 API vs 로컬 `bge-m3` 등)
- [ ] Cross-encoder Reranker 호스팅 방안 — Phase 2 (사내 GPU? 외부 API?)
- [ ] 운영 검토자 인증/권한 — Phase 3
- [ ] Vue 프론트엔드 SSE 이벤트 스키마 합의 (Phase 1 시작 시)
- [ ] Test DB 구성 방식 (Docker Oracle vs 익명화 sample dump)
- [ ] Confidence 임계값 초기값 (`thresholds.yaml`) — Phase 1 운영 데이터 보고 튜닝

---

## 15. 부록: 참고 SOTA 기법 출처

| 영역 | 기법 | 출처 |
|------|------|------|
| Doc RAG | Contextual Retrieval | Anthropic 2024 |
| Doc RAG | Hybrid Search + RRF | Cormack 2009 |
| Doc RAG | Cross-encoder Rerank | BGE 2024 |
| Doc RAG | Parent-Child / RAPTOR | Sarthi 2024 |
| Doc RAG | Adaptive Routing | Jeong 2024 |
| Text2SQL | Schema Linking 분리 | DIN-SQL 2023 |
| Text2SQL | Value Retrieval | CHESS 2024 |
| Text2SQL | Skeleton Few-shot | DAIL-SQL 2023 |
| Text2SQL | Type-aware Self-correction | MAC-SQL 2024 |
| Log | Drain 알고리즘 | He 2017 |
| Orchestration | Plan-and-Execute | Wang 2023 |
| Synthesis | Lost-in-the-middle 회피 | Liu 2023 |
| Quality | RAGAS | RAGAS framework |
