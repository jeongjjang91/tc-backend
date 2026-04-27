# SQLGenerator 이후 동작 정리

## 1. 전체 흐름

SQLGenerator는 자연어 질문과 스키마 정보를 바탕으로 SQL 초안을 만든다.
하지만 이 SQL은 바로 DB에 실행되지 않는다.

SQLGenerator 이후에는 다음 4개 동작이 이어진다.

```text
SQLGenerator.generate()
  -> SQLValidator.validate_and_fix()
  -> DB 실행 / QueryExecutor 관점
  -> SQLRefiner.refine()
  -> ResultInterpreter.interpret()
```

여기서 주의할 점이 있다.

- `QueryExecutor`는 Orchestrator 레벨에서 Agent를 실행하는 공통 실행기다.
- DB Agent 내부의 DB 실행은 `tc_pool.fetch_all()`이 담당한다.

즉, 이름상 executor라고 부를 수 있는 부분이 두 군데 있다.

```text
Orchestrator QueryExecutor
  - db/doc/log/knowledge Agent 중 어떤 Agent를 실행할지 처리

DB Agent 내부 DB 실행
  - 검증된 SQL을 실제 DB pool로 실행
```

이 문서는 SQLGenerator 이후 흐름을 기준으로, Validator, DB 실행, Refiner, Interpreter를 함께 설명한다.

---

## 2. SQLValidator

관련 파일:

- `app/core/agents/db/validator.py`
- `config/whitelist.yaml`
- `tests/unit/test_validator.py`

SQLValidator는 SQLGenerator가 만든 SQL 초안을 실행 가능한 SQL로 볼 수 있는지 검사하는 안전장치다.

SQLGenerator의 결과는 LLM이 만든 문자열이므로 그대로 믿으면 안 된다.
따라서 DB 실행 전에 반드시 Validator를 통과해야 한다.

DB Agent에서는 다음 순서로 호출된다.

```python
validated_sql = self.validator.validate_and_fix(sql)
rows = await self.tc_pool.fetch_all(validated_sql)
```

## 3. SQLValidator 입력과 출력

입력:

```python
sql: str
```

예:

```sql
SELECT PARAM_VALUE
FROM TC_EQP_PARAM
WHERE EQPID = 'EQP_A_001'
  AND PARAM_NAME = 'PARAM_X'
```

출력:

```python
validated_sql: str
```

검증에 실패하면 `SQLValidationError`를 발생시킨다.

## 4. SQLValidator 검증 항목

현재 Validator는 다음 규칙을 적용한다.

### 4-1. SQL 파싱

`sqlglot.parse_one()`으로 SQL을 파싱한다.

```python
tree = sqlglot.parse_one(sql, dialect=self.dialect)
```

파싱에 실패하면 SQL 문법 오류로 본다.

### 4-2. SELECT만 허용

`DELETE`, `UPDATE`, `INSERT`, `DROP` 같은 변경성 SQL은 허용하지 않는다.

```python
if not isinstance(tree, exp.Select):
    raise SQLValidationError(...)
```

### 4-3. 금지 함수 차단

`config/whitelist.yaml`의 `forbidden_functions`에 있는 패턴을 차단한다.

예:

```yaml
forbidden_functions: ["DBMS_", "UTL_", "EXEC"]
```

### 4-4. 테이블 whitelist 검사

SQL에 사용된 테이블이 whitelist에 있어야 한다.

```python
used_tables = {t.name.upper() for t in tree.find_all(exp.Table)}
forbidden_tables = used_tables - self.allowed_tables
```

허용되지 않은 테이블이 있으면 차단한다.

### 4-5. 컬럼 whitelist 검사

SQL에 사용된 컬럼이 해당 테이블의 허용 컬럼 목록에 있어야 한다.

단일 테이블 SQL에서는 table prefix가 없는 컬럼도 그 테이블 소속으로 간주한다.

```python
single_table = next(iter(used_tables)) if len(used_tables) == 1 else None
```

### 4-6. 대용량 테이블 WHERE 필수

`large_tables`에 포함된 테이블은 WHERE 없이 조회할 수 없다.

```yaml
large_tables: [DCOL_LOG, EVENT_HISTORY]
```

### 4-7. requires_where_clause 검사

테이블별로 `requires_where_clause: true`가 설정되어 있으면 WHERE가 반드시 필요하다.

예:

```yaml
TC_EQP_PARAM:
  requires_where_clause: true
```

### 4-8. row limit 자동 주입

현재 구현은 LIMIT이나 ROWNUM이 없으면 row 제한을 자동으로 주입한다.

```python
if not tree.find(exp.Limit) and "ROWNUM" not in sql_upper:
    sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= 1000"
```

주의점:

현재 SQLGenerator prompt는 MySQL 8.0 기준이고, Validator는 `ROWNUM`을 주입한다.
즉 MySQL과 Oracle 문법이 섞여 있다.
운영 전에는 DB dialect를 하나로 통일해야 한다.

---

## 5. DB 실행 / Executor 관점

SQLValidator를 통과한 SQL은 DB pool로 실행된다.

DB Agent 내부 코드:

```python
validated_sql = self.validator.validate_and_fix(sql)
rows = await self.tc_pool.fetch_all(validated_sql)
```

여기서 `tc_pool`은 실제 TC DB에 접근하는 pool이다.
현재 `app/api/deps.py`에서는 `MySQLPool`이 주입된다.

DB pool의 역할:

- SQL 실행
- timeout 처리
- DB driver 오류를 `DBExecutionError`로 변환
- 최대 row 수 제한

## 6. Orchestrator QueryExecutor와의 차이

`app/core/orchestrator/executor.py`의 `QueryExecutor`는 SQL을 실행하는 컴포넌트가 아니다.
이 컴포넌트는 Agent 자체를 실행한다.

흐름:

```text
Planner
  -> SubQuery(agent="db", query="...")
  -> QueryExecutor
  -> DBAgent.run()
```

`QueryExecutor`는 다음 역할을 한다.

1. `sub_query.agent`에 맞는 Agent 인스턴스를 찾는다.
2. `agent.run(sub_query, context)`를 실행한다.
3. 여러 sub_query가 있으면 `asyncio.gather()`로 병렬 실행한다.
4. Agent 실행 중 예외가 나면 실패 `AgentResult`로 감싼다.

코드:

```python
tasks.append(self._run_safe(agent, sq, context))
return list(await asyncio.gather(*tasks))
```

정리하면:

```text
QueryExecutor = Agent 실행 라우터 + 병렬 실행기 + 예외 격리 계층
tc_pool.fetch_all = 검증된 SQL을 실제 DB에 실행하는 계층
```

DB Agent 내부 동작을 설명할 때는 `tc_pool.fetch_all()`을 DB 실행 단계로 보면 된다.
시스템 전체 흐름을 설명할 때는 `QueryExecutor`를 Agent 실행 단계로 보면 된다.

---

## 7. SQLRefiner

관련 파일:

- `app/core/agents/db/refiner.py`
- `config/prompts/sql_refiner.j2`

SQLRefiner는 SQLGenerator가 만든 SQL이 실패했을 때, 이전 SQL과 오류 정보를 바탕으로 SQL을 다시 작성하는 LLM wrapper다.

Validator 또는 DB 실행 단계에서 문제가 발생하면 Refiner가 호출된다.

Refiner가 호출되는 경우:

1. SQL validation 실패
2. DB execution 실패
3. 조회 결과가 0건

DB Agent의 refine loop:

```python
for attempt in range(self.max_refine + 1):
    try:
        validated_sql = self.validator.validate_and_fix(sql)
        rows = await self.tc_pool.fetch_all(validated_sql)

        if not rows and attempt < self.max_refine:
            refined = await self.refiner.refine(...)
            sql = refined.get("sql", sql)
            continue
        break

    except SQLValidationError as e:
        refined = await self.refiner.refine(...)
        sql = refined.get("sql", sql)

    except DBExecutionError as e:
        refined = await self.refiner.refine(...)
        sql = refined.get("sql", sql)
```

기본 refine 횟수:

```yaml
max_refine_attempts: 2
```

## 8. SQLRefiner 입력과 출력

입력:

```python
question: str
previous_sql: str
error_type: str
error_message: str
allowed_tables: list[str]
```

예:

```python
await refiner.refine(
    question="A 설비 PARAM_X 값 알려줘",
    previous_sql="SELECT PARAM_VALUE FROM WRONG_TABLE",
    error_type="validation_error",
    error_message="허용되지 않은 테이블: WRONG_TABLE",
    allowed_tables=["TC_EQP_PARAM", "TC_EQUIPMENT"],
)
```

출력:

```json
{
  "reasoning": "수정 이유",
  "sql": "수정된 SQL",
  "confidence": 0.8
}
```

DB Agent는 반환된 SQL을 다음 attempt에서 다시 Validator에 넣는다.

## 9. error_type별 역할

현재 정의된 error type hint는 다음과 같다.

```python
REFINEMENT_HINTS = {
    "syntax_error": "...",
    "empty_result": "...",
    "too_many_rows": "...",
    "validation_error": "...",
}
```

의미:

- `syntax_error`: DB가 SQL 문법 오류를 반환한 경우
- `empty_result`: SQL 실행은 성공했지만 결과가 0건인 경우
- `too_many_rows`: 결과가 너무 많은 경우
- `validation_error`: whitelist, SELECT, WHERE 조건 등 내부 검증에서 실패한 경우

현재 DB Agent에서는 주로 다음 세 가지가 사용된다.

```text
validation_error
syntax_error
empty_result
```

주의점:

`DBExecutionError`가 모두 `syntax_error`로 넘어가는데, 실제로는 timeout, 권한 문제, connection 문제일 수도 있다.
운영 품질을 높이려면 DBExecutionError를 세분화하는 것이 좋다.

---

## 10. ResultInterpreter

관련 파일:

- `app/core/agents/db/interpreter.py`
- `config/prompts/synthesizer.j2`

ResultInterpreter는 SQL 실행 결과 rows를 사용자에게 보여줄 자연어 답변으로 바꾸는 단계다.

SQLGenerator와 Refiner가 SQL을 만드는 LLM wrapper라면, ResultInterpreter는 SQL 결과를 설명하는 LLM wrapper다.

현재 구현:

```python
async def interpret(self, question: str, sql: str, rows: list[dict]) -> dict:
    prompt = self.renderer.render(
        "synthesizer",
        question=question,
        sql=sql,
        rows=rows[: self.max_rows_in_prompt],
        row_count=len(rows),
    )
    return await self.llm.complete_json(prompt)
```

기본값:

```python
max_rows_in_prompt = 20
```

즉 전체 rows가 1000건이어도 LLM prompt에는 앞 20건만 들어간다.
다만 `row_count`는 전체 row 개수로 전달된다.

## 11. ResultInterpreter 출력

현재 prompt가 기대하는 출력 구조:

```json
{
  "answer": "인용 포함 답변 [row_1]...",
  "confidence": 0.0,
  "needs_human_review": false,
  "missing_info": []
}
```

DB Agent는 interpreter 결과에서 `answer`와 `confidence`를 사용한다.

```python
interp = await self.interpreter.interpret(question, sql, rows)
answer = interp.get("answer", "")
confidence = min(gen_confidence, interp.get("confidence", gen_confidence))
```

최종 confidence는 SQL 생성 confidence와 interpreter confidence 중 낮은 값이다.

```text
final confidence = min(sql_generation_confidence, interpretation_confidence)
```

## 12. Evidence 생성

ResultInterpreter 이후 DB Agent는 rows를 Evidence로 변환한다.

```python
evidences = [
    Evidence(
        id=f"row_{i+1}",
        source_type="db_row",
        content=str(row),
        metadata={"sql": sql, "row_index": i},
    )
    for i, row in enumerate(rows)
]
```

이 evidence는 Chat API에서 citation 이벤트로 전달된다.

```text
event: citation
data: {"citations": [...]}
```

현재 prompt는 answer 안에도 `[row_1]` 같은 citation marker를 넣도록 요구한다.
하지만 최근 개선 문서에서는 answer에서는 `[row_N]`을 제거하고, evidence/citation payload로 근거를 전달하는 방향을 제안한다.

---

## 13. 최종 AgentResult 반환

DB Agent는 마지막에 `AgentResult`를 반환한다.

```python
return AgentResult(
    sub_query_id=sub_query.id,
    success=True,
    evidence=evidences,
    raw_data={"sql": sql, "rows": rows, "answer": answer},
    confidence=confidence,
)
```

현재 raw_data:

```json
{
  "sql": "SELECT ...",
  "rows": [...],
  "answer": "..."
}
```

개선 여지:

```json
{
  "sql": "SELECT ...",
  "rows": [...],
  "answer": "...",
  "table": {
    "headers": [...],
    "rows": [...],
    "row_count": 1000,
    "truncated": true
  },
  "notice": "결과가 1000건으로 제한되었습니다.",
  "needs_human_review": false
}
```

---

## 14. 4개 컴포넌트 요약

| 단계 | 파일 | 역할 | 실패 시 |
| --- | --- | --- | --- |
| SQLValidator | `validator.py` | SQL 안전성 검증, whitelist 검사, row limit 보정 | `SQLValidationError` 발생 |
| DB 실행 / Executor | `tc_pool.fetch_all`, `orchestrator/executor.py` | 검증 SQL 실행 / Agent 실행 라우팅 | `DBExecutionError` 또는 실패 `AgentResult` |
| SQLRefiner | `refiner.py` | 실패 SQL을 오류 정보 기반으로 재작성 | max_refine 초과 시 실패 반환 |
| ResultInterpreter | `interpreter.py` | rows를 자연어 답변으로 요약 | 낮은 confidence 또는 review 필요 |

## 15. 한 줄 요약

SQLGenerator 이후 단계는 LLM이 만든 SQL 초안을 안전하게 실행 가능한 결과로 바꾸는 구간이다.
Validator가 위험 SQL을 막고, DB 실행이 실제 rows를 가져오며, Refiner가 실패 SQL을 재작성하고, Interpreter가 rows를 사용자 답변으로 바꾼다.

```text
SQL 초안
  -> 안전성 검증
  -> DB 실행
  -> 실패 시 재작성
  -> 자연어 답변과 Evidence 생성
```
