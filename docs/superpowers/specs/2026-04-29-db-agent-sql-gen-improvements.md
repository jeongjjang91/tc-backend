# DB Agent SQL 생성 개선 설계

- 작성일: 2026-04-29
- 상태: 설계
- 관련 문서:
  - `2026-04-27-schema-linker-bge-m3-bm25-hybrid.md` — SchemaLinker 검색 개선
  - `2026-04-21-text-to-sql-improvement-and-eval-system.md` — T6 (sql_gen 프롬프트 개선), T9 (execution-guided verification)

---

## 0. 요약

DB Agent의 SQL 생성 파이프라인에서 발견된 두 가지 문제를 다룬다.

| # | 문제 | 위치 | 영향 |
|---|------|------|------|
| A | `linked`(컬럼·JOIN 힌트)가 `SQLGenerator`에 전달되지만 프롬프트에 주입되지 않아 버려짐 | `sql_generator.py`, `sql_gen.j2` | JOIN 오류 증가, 컬럼 추론 부담 |
| B | `empty_result`(rows=0)에서 Refiner를 호출해 정상 SQL이 잘못된 SQL로 교체될 수 있음 | `db/agent.py` | 데이터 없음을 오류로 오판, 잘못된 데이터 반환 위험 |

---

## 1. 문제 A — `linked` 힌트 미주입

### 1-1. `SchemaLinker.link()` 반환값

`config/prompts/schema_linker.j2` 기준 출력:

```json
{
  "tables": ["TC_EQP_PARAM", "TC_LINE_STATUS"],
  "columns": ["TC_EQP_PARAM.EQP_ID", "TC_EQP_PARAM.PARAM_VAL", "TC_LINE_STATUS.LINE_ID"],
  "joins": ["TC_EQP_PARAM.LINE_ID = TC_LINE_STATUS.LINE_ID"]
}
```

### 1-2. 현재 구조

```python
async def generate(self, question: str, schema_subset: str, linked: dict) -> dict:
    ...
    prompt = self.renderer.render(
        "sql_gen",
        schema_subset=schema_subset,
        question=question,
        few_shots=few_shots,
        value_candidates=value_candidates,
        # linked → 여기서 버려짐
    )
```

`schema_subset`은 `linked.tables`로 필터링된 테이블의 **전체 컬럼 텍스트**다. SQL Gen LLM은 이 텍스트에서 다시 컬럼을 추론해야 한다. SchemaLinker가 이미 좁혀놓은 결과를 재활용하지 않는 구조다.

### 1-3. 왜 문제인가

| 항목 | 현재 | 개선 후 |
|------|------|---------|
| 컬럼 추론 | SQL Gen LLM이 `schema_subset`에서 직접 선택 | SchemaLinker 힌트 → 추론 부담 감소 |
| JOIN 조건 | SQL Gen LLM이 스스로 추론 | SchemaLinker가 제안한 조건 제공 → 오류 감소 |
| `linked` 활용 | 파라미터만 있고 미사용 | 프롬프트에 명시적 주입 |

JOIN 오류는 테이블 수가 2개 이상일 때 SQL 실행 실패의 주요 원인이다. `linked.joins`를 힌트로 제공하면 Refine 루프 진입 빈도를 낮출 수 있다.

### 1-4. 설계

**`sql_gen.j2` 힌트 섹션 추가**

위치: `[관련 스키마]` 섹션 바로 뒤, `[유사 예시]` 섹션 앞.

```jinja2
{% if linked_columns or linked_joins %}
[SchemaLinker 힌트]
{% if linked_columns %}
관련 컬럼: {{ linked_columns | join(", ") }}
{% endif %}
{% if linked_joins %}
JOIN 조건 후보: {{ linked_joins | join(" / ") }}
{% endif %}
{% endif %}
```

**힌트임을 명시하는 이유:** LLM이 `schema_subset`을 무시하고 힌트만 따르는 부작용 방지. "후보"/"힌트" 표현으로 LLM이 재검토할 여지를 둔다.

**`SQLGenerator.generate()` 변경**

```python
async def generate(self, question: str, schema_subset: str, linked: dict) -> dict:
    few_shots = self.few_shot_store.search(question, top_k=self.few_shot_top_k)
    value_candidates = self.value_store.extract_from_question(question)
    prompt = self.renderer.render(
        "sql_gen",
        schema_subset=schema_subset,
        question=question,
        few_shots=few_shots,
        value_candidates=value_candidates,
        linked_columns=linked.get("columns", []),
        linked_joins=linked.get("joins", []),
    )
    return await self.llm.complete_json(prompt)
```

시그니처 변경 없음. `linked`가 빈 dict이거나 키가 없으면 Jinja2 조건 블록이 렌더링하지 않아 기존 동작과 동일하다.

---

## 2. 문제 B — empty_result에서 Refiner 호출

### 2-1. 현재 구조

```python
rows = await self.tc_pool.fetch_all(validated_sql)

if not rows and attempt < self.max_refine:
    log.info("empty_result_refine", attempt=attempt)
    refined = await self.refiner.refine(
        question, sql, "empty_result", "결과 0건", list(self.validator.allowed_tables)
    )
    sql = refined.get("sql", sql)
    continue
```

Refiner의 `empty_result` 힌트:
> "WHERE 조건이 너무 좁을 수 있습니다. 조건을 완화하거나 LIKE 패턴을 사용해보세요."

### 2-2. 왜 문제인가

`rows = 0`은 두 케이스를 구분하지 못한다:

| 케이스 | 실제 원인 | Refiner 호출 결과 |
|--------|----------|-----------------|
| "L99 라인 설비 목록" (L99가 DB에 없음) | 데이터 없음, SQL은 정상 | WHERE 조건 완화 → **잘못된 데이터 반환** |
| "L01 라인 설비 목록" (값 오타 등) | SQL 논리 오류 | 조건 완화로 우연히 개선될 수도 있음 |

실제 운영에서는 첫 번째 케이스가 훨씬 많다. Refiner가 정상 SQL을 잘못된 SQL로 교체하는 역효과가 발생하고, LLM 호출 비용도 낭비된다.

### 2-3. 설계

**`empty_result` Refiner 호출 제거**

```python
rows = await self.tc_pool.fetch_all(validated_sql)
if not rows:
    break  # SQL 성공, 데이터 없음 → Interpreter가 처리
```

`syntax_error` / `validation_error`만 Refiner 대상으로 유지한다. `rows = 0`은 SQL이 정상 실행된 것이므로 Interpreter가 "조회 결과가 없습니다" 형태로 자연어 응답을 생성하면 된다.

**변경 후 Refine 루프 트리거**

| 트리거 | Refiner 호출 | 근거 |
|--------|-------------|------|
| `SQLValidationError` | O | 화이트리스트 위반 등 명확한 오류 |
| `DBExecutionError` | O | DB 실행 실패, SQL 문법 문제 |
| `rows = 0` | **X** | 데이터 없음은 오류가 아님 |

---

## 3. 변경 범위

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `config/prompts/sql_gen.j2` | 수정 | `linked_columns`, `linked_joins` 힌트 섹션 추가 |
| `app/core/agents/db/sql_generator.py` | 수정 | `render()` 호출에 `linked_columns`, `linked_joins` 전달 |
| `app/core/agents/db/agent.py` | 수정 | `empty_result` Refiner 호출 제거 |
| `tests/unit/test_sql_generator.py` | 수정 | `linked` 포함 케이스 추가 |
| `tests/unit/test_db_agent.py` | 수정 | `rows=0` 시 Refiner 미호출 검증 |
| `tests/golden/sql_gen_cases.yaml` | 수정 | JOIN 힌트 활용, empty_result 케이스 추가 |

`SchemaLinker`, `SQLRefiner`, `Interpreter`는 변경 없음.

---

## 4. Golden Eval 케이스

```yaml
# --- 문제 A: linked 힌트 주입 ---

- id: SG_LINKED_001
  description: "JOIN 힌트 활용 — linked.joins 제공 시 올바른 JOIN 생성"
  question: "L01 라인의 설비 파라미터 평균값 조회"
  linked:
    tables: ["TC_EQP_PARAM", "TC_LINE_STATUS"]
    columns: ["TC_EQP_PARAM.PARAM_VAL", "TC_LINE_STATUS.LINE_ID"]
    joins: ["TC_EQP_PARAM.LINE_ID = TC_LINE_STATUS.LINE_ID"]
  expected_keywords: ["JOIN", "TC_EQP_PARAM", "TC_LINE_STATUS", "AVG"]
  tags: [join, linked_hint]

- id: SG_LINKED_002
  description: "컬럼 힌트 — linked.columns 제공 시 올바른 컬럼 선택"
  question: "설비별 파라미터 값 조회"
  linked:
    tables: ["TC_EQP_PARAM"]
    columns: ["TC_EQP_PARAM.EQP_ID", "TC_EQP_PARAM.PARAM_VAL"]
    joins: []
  expected_keywords: ["EQP_ID", "PARAM_VAL"]
  tags: [column_hint, linked_hint]

- id: SG_LINKED_003
  description: "linked 빈 값 — 기존 동작과 동일"
  question: "설비 목록 조회"
  linked:
    tables: []
    columns: []
    joins: []
  expected_keywords: ["SELECT"]
  tags: [regression, empty_linked]

# --- 문제 B: empty_result Refiner 제거 ---

- id: SG_EMPTY_001
  description: "존재하지 않는 값 조회 — rows=0이어도 SQL 정상, Refiner 미호출"
  question: "L99 라인 설비 목록"
  expected_rows: []
  expected_no_refine: true
  tags: [empty_result, no_refine]

- id: SG_EMPTY_002
  description: "정상 데이터 없음 응답 — Interpreter가 '결과 없음' 처리"
  question: "존재하지 않는 설비 X999 파라미터 조회"
  expected_rows: []
  expected_answer_contains: ["없"]
  tags: [empty_result, interpreter]
```

---

## 5. 주의사항

- **SchemaLinker 품질 의존성(A):** `linked.joins`가 틀렸을 때 SQL Gen LLM도 영향을 받을 수 있다. 프롬프트에 "후보"로 명시해 LLM이 override할 여지를 남긴다.
- **schema_linker.j2 출력 안정성(A):** LLM이 `joins` 키를 항상 반환하지 않을 수 있다. `linked.get("joins", [])` 방어 처리 필수.
- **Interpreter empty 처리(B):** `rows=[]`가 들어올 때 Interpreter가 자연스러운 응답을 생성하는지 확인 필요. `interpreter.j2` 프롬프트에 empty 케이스 예시가 없으면 추가 검토.
- **Golden Eval 우선:** 프롬프트 변경이므로 PR 전 Golden Eval 통과 필수 (baseline -5% 기준).
