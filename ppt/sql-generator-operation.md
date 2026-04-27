# SQLGenerator 동작 설명

## 1. SQLGenerator란?

SQLGenerator는 DB Agent 파이프라인에서 자연어 질문을 SQL 초안으로 바꾸는 단계다.

관련 파일:

- `app/core/agents/db/sql_generator.py`
- `app/core/agents/db/agent.py`
- `app/infra/db/few_shot_store.py`
- `app/infra/db/value_store.py`
- `config/prompts/sql_gen.j2`
- `config/few_shot/sql_seed.yaml`

DB Agent 전체 흐름에서 SQLGenerator의 위치는 다음과 같다.

```text
User Question
  -> SchemaStore.search()
  -> SchemaLinker.link()
  -> schema_subset 구성
  -> SQLGenerator.generate()
  -> SQLValidator.validate_and_fix()
  -> DB 실행
  -> SQLRefiner.refine() if needed
```

SQLGenerator는 SQL을 생성하지만, 생성된 SQL을 바로 실행하지는 않는다.
생성 SQL은 반드시 Validator를 통과한 뒤 DB에 실행된다.

## 2. 핵심 역할

SQLGenerator의 역할은 다음 네 가지 정보를 조합해 LLM에게 SQL 생성을 요청하는 것이다.

1. 사용자 질문
2. SchemaLinker 이후 좁혀진 `schema_subset`
3. 유사 질문 기반 few-shot SQL 예시
4. 질문 속 값 후보

최종적으로 LLM에게 JSON 형식의 응답을 받는다.

의도된 출력 구조:

```json
{
  "reasoning": "단계별 요약",
  "sql": "SELECT ...",
  "confidence": 0.0,
  "assumptions": []
}
```

## 3. 코드 기준 동작

핵심 구현은 `SQLGenerator.generate()`에 있다.

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
    )
    return await self.llm.complete_json(prompt)
```

순서:

1. 질문과 유사한 few-shot 예시를 검색한다.
2. 질문 속 토큰에서 값 후보를 추출한다.
3. `sql_gen.j2` prompt를 렌더링한다.
4. LLM에게 JSON 응답을 요청한다.
5. SQL 생성 결과를 dict로 반환한다.

## 4. 입력값

`generate()`의 입력은 세 개다.

```python
generate(question, schema_subset, linked)
```

### question

사용자의 원본 질문이다.

예:

```text
A 설비의 PARAM_X 값 알려줘
```

### schema_subset

SchemaStore와 SchemaLinker를 거쳐 좁혀진 스키마 정보다.
SQLGenerator가 실제로 참고하는 테이블과 컬럼 설명이다.

예:

```text
테이블 TC_EQP_PARAM
  - LINEID (...)
  - EQPID (...)
  - SERVER_MODEL (...)
  - PARAM_NAME (...)
  - PARAM_VALUE (...)
```

### linked

SchemaLinker가 반환한 결과다.

```json
{
  "tables": ["TC_EQP_PARAM"],
  "columns": ["TC_EQP_PARAM.EQPID", "TC_EQP_PARAM.PARAM_NAME"],
  "joins": []
}
```

주의할 점은 현재 구현에서 `linked`는 `generate()` 인자로 받지만 prompt 렌더링에는 직접 사용되지 않는다는 것이다.
현재 SQLGenerator는 `schema_subset`, `question`, `few_shots`, `value_candidates`만 prompt에 넣는다.

## 5. FewShotStore 동작

관련 파일:

- `app/infra/db/few_shot_store.py`

FewShotStore는 이전에 준비된 Q-SQL 예시 또는 성공한 SQL 예시를 저장하고, 현재 질문과 유사한 예시를 검색한다.

SQLGenerator는 다음 코드로 few-shot 예시를 가져온다.

```python
few_shots = self.few_shot_store.search(question, top_k=self.few_shot_top_k)
```

기본값:

```yaml
few_shot_top_k: 3
```

### skeleton 변환

FewShotStore는 질문을 그대로 비교하지 않고 skeleton으로 일반화한다.

예:

```text
A 설비의 PARAM_X 값 알려줘
```

다음과 같이 바뀔 수 있다.

```text
<EQP> 설비의 <PARAM> 값 알려줘
```

관련 함수:

```python
extract_skeleton(question)
```

일반화 규칙 예:

- `EQP_A_001` 같은 설비 코드를 `<EQP>`로 치환
- `PARAM_X` 같은 파라미터명을 `<PARAM>`으로 치환
- `DCOL_*` 형태를 `<DCOL>`로 치환

이렇게 하면 실제 값은 달라도 질문 패턴이 비슷한 예시를 찾을 수 있다.

### few-shot 검색 방식

FewShotStore도 TF-IDF 문자 n-gram을 사용한다.

```python
TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
```

검색 흐름:

1. seed 예시 질문을 skeleton으로 변환한다.
2. skeleton 문장들로 TF-IDF 인덱스를 만든다.
3. 사용자 질문도 skeleton으로 변환한다.
4. cosine similarity로 유사 예시를 찾는다.
5. 점수가 0보다 큰 예시만 반환한다.

반환 예시:

```python
[
    {
        "question": "A 설비의 PARAM_X 값 알려줘",
        "sql": "SELECT PARAM_VALUE FROM TC_EQP_PARAM WHERE ...",
        "source": "seed"
    }
]
```

## 6. Few-shot이 SQL 생성에 주는 효과

Few-shot 예시는 LLM에게 다음 정보를 제공한다.

- 이 프로젝트에서 자주 쓰는 SQL 패턴
- 설비/파라미터 질문을 WHERE 조건으로 바꾸는 방식
- 테이블명과 컬럼명을 사용하는 관례
- JOIN 또는 집계가 필요한 질문의 예시

예를 들어 유사 예시가 다음과 같다면:

```text
질문: A 설비의 PARAM_X 값 알려줘
SQL: SELECT PARAM_VALUE FROM TC_EQP_PARAM
     WHERE EQPID = 'EQP_A_001'
       AND PARAM_NAME = 'PARAM_X'
```

새 질문이 다음과 같을 때:

```text
B 설비의 PARAM_Y 값 알려줘
```

LLM은 같은 SQL 구조를 재사용할 가능성이 높아진다.

## 7. 성공 SQL 자동 캐시

DB Agent는 SQL 실행과 결과 해석이 성공하고 confidence가 기준 이상이면 성공한 Q-SQL을 FewShotStore에 추가한다.

관련 흐름:

```python
if confidence >= self.confidence_threshold and rows:
    self.few_shot_store.add_success(question, sql)
```

즉, 운영 중 성공한 질문과 SQL이 다음 요청의 few-shot 예시로 재사용될 수 있다.

기본 confidence 기준:

```yaml
confidence_auto_send: 0.70
```

## 8. ValueStore 동작

관련 파일:

- `app/infra/db/value_store.py`

ValueStore는 질문 안에 등장한 토큰과 알려진 값 목록을 비교해서 후보 값을 찾는다.

SQLGenerator는 다음 코드로 값 후보를 가져온다.

```python
value_candidates = self.value_store.extract_from_question(question)
```

ValueStore의 목적은 사용자가 말한 표현과 DB에 실제 저장된 값이 조금 다를 때, LLM에게 후보를 제공하는 것이다.

예:

```text
질문: A 설비 PARAM_X 알려줘
```

값 후보:

```python
{
    "PARAM_X": ["PARAM_X", "PARAM_X_OLD", "PARAM_X_NEW"]
}
```

## 9. ValueStore의 후보 검색 방식

ValueStore는 세 단계로 후보를 찾는다.

### 9-1. difflib fuzzy match

먼저 Python의 `difflib.get_close_matches()`를 사용한다.

```python
difflib.get_close_matches(term, all_values, n=top_n, cutoff=0.2)
```

문자열이 어느 정도 비슷하면 후보로 반환한다.

### 9-2. trigram fallback

fuzzy match 결과가 없으면 trigram overlap을 사용한다.

예:

```text
PARAM_X -> PAR, ARA, RAM, AM_, M_X
```

질문 토큰과 저장 값의 trigram 조각이 많이 겹치면 후보로 본다.

### 9-3. substring fallback

trigram도 실패하면 알파벳/숫자 토큰을 뽑아 부분 문자열 포함 여부를 본다.

예:

```text
질문 토큰: PARAM
저장 값: EQP_A_PARAM_X
```

`PARAM`이 저장 값 안에 포함되어 있으면 후보가 될 수 있다.

## 10. Prompt 구성

관련 파일:

- `config/prompts/sql_gen.j2`

SQLGenerator는 다음 값을 prompt에 넣는다.

```jinja2
{{ schema_subset }}
{{ few_shots }}
{{ value_candidates }}
{{ question }}
```

prompt는 LLM에게 다음 사고 단계를 요구한다.

1. 어떤 테이블이 필요한가?
2. 어떤 컬럼이 필요한가?
3. JOIN 조건은 무엇인가?
4. WHERE 조건과 사용할 값은 무엇인가?
5. 결과를 어떻게 제한할 것인가?

최종 출력은 JSON만 요구한다.

```json
{
  "reasoning": "단계별 요약",
  "sql": "MySQL 8.0 SQL 문",
  "confidence": 0.0,
  "assumptions": []
}
```

## 11. DB Agent에서 SQLGenerator 결과를 쓰는 방식

DB Agent는 SQLGenerator 결과에서 `sql`과 `confidence`를 꺼낸다.

```python
sql_result = await self.generator.generate(question, schema_subset, linked)
sql = sql_result.get("sql", "")
gen_confidence = sql_result.get("confidence", 0.0)
```

그 다음 이 SQL을 validator에 넘긴다.

```python
validated_sql = self.validator.validate_and_fix(sql)
rows = await self.tc_pool.fetch_all(validated_sql)
```

정리하면:

```text
SQLGenerator 출력
  -> sql 문자열 추출
  -> confidence 추출
  -> SQLValidator 검증
  -> DB 실행
```

SQLGenerator는 "초안 생성기"이고, 안전성 판단은 Validator가 맡는다.

## 12. 예시 시나리오

질문:

```text
A 설비의 PARAM_X 값 알려줘
```

### 12-1. schema_subset

SchemaLinker 이후 SQLGenerator는 다음과 같은 스키마 후보를 받는다.

```text
테이블 TC_EQP_PARAM
  - LINEID
  - EQPID
  - SERVER_MODEL
  - PARAM_NAME
  - PARAM_VALUE
```

### 12-2. few-shot 예시

FewShotStore에서 유사 예시를 찾는다.

```text
질문: B 설비의 PARAM_Y 값 알려줘
SQL: SELECT PARAM_VALUE
     FROM TC_EQP_PARAM
     WHERE EQPID = 'EQP_B_001'
       AND PARAM_NAME = 'PARAM_Y'
```

### 12-3. 값 후보

ValueStore가 후보 값을 찾을 수 있다.

```python
{
    "PARAM_X": ["PARAM_X"]
}
```

### 12-4. LLM 출력

SQLGenerator는 LLM에게 JSON 응답을 받는다.

```json
{
  "reasoning": "TC_EQP_PARAM에서 설비 ID와 파라미터명을 조건으로 PARAM_VALUE를 조회한다.",
  "sql": "SELECT PARAM_VALUE FROM TC_EQP_PARAM WHERE EQPID = 'EQP_A_001' AND PARAM_NAME = 'PARAM_X'",
  "confidence": 0.86,
  "assumptions": ["A 설비는 EQP_A_001로 매핑된다고 가정"]
}
```

이 SQL은 이후 Validator를 통과해야 실제 DB에 실행된다.

## 13. SQLGenerator와 Validator의 역할 차이

SQLGenerator와 Validator는 역할이 다르다.

| 구성요소 | 역할 | 실패 시 |
| --- | --- | --- |
| SQLGenerator | 자연어와 스키마를 바탕으로 SQL 초안 생성 | 낮은 confidence 또는 잘못된 SQL 생성 가능 |
| SQLValidator | SQL 문법, 허용 테이블/컬럼, WHERE 조건, 금지 함수 검증 | ValidationError 발생 |
| SQLRefiner | 검증/실행 실패 SQL을 오류 정보 기반으로 재작성 | 최대 횟수 초과 시 실패 반환 |

핵심은 SQLGenerator가 만든 SQL을 신뢰하지 않고, 반드시 Validator와 refine loop를 거친다는 점이다.

## 14. 현재 구현상 주의점

### 14-1. `linked` 인자가 직접 사용되지 않음

`generate(question, schema_subset, linked)`는 `linked`를 인자로 받지만 현재 prompt 렌더링에는 넘기지 않는다.

현재 사용되는 값:

```python
schema_subset=schema_subset
question=question
few_shots=few_shots
value_candidates=value_candidates
```

개선 방향:

- `linked["columns"]`를 prompt에 명시적으로 포함
- `linked["joins"]`를 JOIN 후보로 전달
- LLM이 SchemaLinker 결과를 더 강하게 따르도록 prompt 규칙 추가

### 14-2. ValueStore가 현재 비어 있을 가능성

`ValueStore`에는 `load_values()` 메서드가 있지만, 현재 의존성 조립 코드에서 실제 값 목록을 로드하는 흐름은 뚜렷하게 보이지 않는다.

값 목록이 비어 있으면:

```python
value_candidates = {}
```

가 되어 prompt에 값 후보가 들어가지 않는다.

개선 방향:

- 운영 DB에서 자주 쓰는 설비 ID, 파라미터명, 상태값 등을 주기적으로 로드
- 컬럼별 value dictionary 구성
- `value_retrieval_top_n` 설정과 실제 top_n 사용값 정렬

### 14-3. Prompt 인코딩 깨짐

`config/prompts/sql_gen.j2`의 한국어 문구가 깨져 있다.
LLM이 prompt를 읽고 SQL을 생성해야 하므로 품질에 직접 영향을 줄 수 있다.

개선 방향:

- UTF-8 기준으로 prompt 복구
- 출력 JSON schema 명확화
- 잘못된 DB dialect 문구 제거

### 14-4. DB dialect 혼재

`sql_gen.j2`는 MySQL 8.0 규칙을 설명한다.
하지만 다른 파일에는 Oracle 기준 문구와 `ROWNUM` 보정이 남아 있다.

개선 방향:

- 실제 대상 DB를 MySQL 또는 Oracle 중 하나로 확정
- SQLGenerator prompt, SQLValidator dialect, row limit 보정 방식 통일

### 14-5. SQL confidence 검증 약함

SQLGenerator가 반환한 confidence는 후속 계산에 사용된다.
하지만 confidence 값 자체가 LLM이 생성한 자기평가이므로 신뢰도 검증에는 한계가 있다.

개선 방향:

- confidence만 보지 말고 validator 통과 여부, row count, interpreter confidence를 함께 사용
- low confidence SQL은 human review 또는 추가 refine 대상으로 처리

## 15. 개선된 SQLGenerator prompt 예시

현재 prompt를 복구한다면 다음 구조가 적합하다.

```jinja2
당신은 {{ dialect }} SQL 전문가입니다.
사용자 질문에 답하기 위한 SELECT SQL만 생성하세요.

[사용 가능한 스키마]
{{ schema_subset }}

[SchemaLinker 선택 결과]
Tables: {{ linked.tables }}
Columns: {{ linked.columns }}
Joins: {{ linked.joins }}

{% if few_shots %}
[유사 예시]
{% for ex in few_shots %}
질문: {{ ex.question }}
SQL: {{ ex.sql }}
{% endfor %}
{% endif %}

{% if value_candidates %}
[값 후보]
{% for term, candidates in value_candidates.items() %}
- "{{ term }}" 후보: {{ candidates }}
{% endfor %}
{% endif %}

[사용자 질문]
{{ question }}

[규칙]
- SELECT 문만 생성하세요.
- 제공된 스키마에 없는 테이블과 컬럼은 사용하지 마세요.
- 대용량 테이블은 WHERE 조건 없이 조회하지 마세요.
- 결과는 최대 1000건으로 제한하세요.
- JSON만 출력하세요.

[출력 형식]
{
  "reasoning": "테이블, 컬럼, 조건 선택 이유",
  "sql": "SELECT ...",
  "confidence": 0.0,
  "assumptions": []
}
```

## 16. 운영 품질을 높이기 위한 체크리스트

1. Few-shot seed 보강
   - 실제 운영자가 자주 묻는 질문 패턴 추가
   - 설비 조회, 파라미터 조회, 상태 조회, 기간 조건, 집계 질문 포함

2. ValueStore 데이터 로드
   - 설비 ID
   - 파라미터명
   - 상태값
   - 모델명
   - 라인 ID

3. Prompt 복구
   - 깨진 한국어 복원
   - DB dialect 통일
   - JSON 출력 규칙 강화

4. `linked` 활용 강화
   - SchemaLinker의 columns, joins를 SQLGenerator prompt에 포함
   - SQL 생성이 SchemaLinker 선택 결과를 벗어났는지 검증

5. Golden test 확장
   - 질문별 기대 SQL 패턴 추가
   - Validator 통과 여부 확인
   - row evidence 생성 여부 확인

## 17. 한 줄 요약

SQLGenerator는 SchemaLinker가 좁힌 스키마, few-shot 예시, 값 후보, 사용자 질문을 하나의 prompt로 묶어 LLM에게 SQL 초안을 생성하게 하는 단계다.
생성된 SQL은 신뢰 대상이 아니라 검증 대상이며, 이후 SQLValidator와 SQLRefiner를 거쳐야 실제 DB 실행까지 갈 수 있다.
