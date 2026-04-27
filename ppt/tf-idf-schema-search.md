# TF-IDF 기반 Schema 검색 동작 설명

## 1. TF-IDF란?

TF-IDF는 어떤 문서가 사용자 질문과 얼마나 관련 있어 보이는지 계산하는 전통적인 검색 방식이다.
이 프로젝트에서는 `SchemaStore`가 질문과 DB 테이블 설명을 비교해서 관련 테이블 후보를 고르는 데 사용한다.

TF-IDF는 두 값을 곱해서 중요도를 계산한다.

```text
TF-IDF = TF * IDF
```

## 2. TF와 IDF

### TF: Term Frequency

TF는 특정 단어 또는 문자열 조각이 한 문서 안에 얼마나 자주 등장하는지를 의미한다.

예를 들어 어떤 테이블 설명에 `PARAM` 관련 표현이 여러 번 등장하면, 해당 테이블 문서에서 `PARAM`의 TF 값은 높아진다.

### IDF: Inverse Document Frequency

IDF는 특정 단어 또는 문자열 조각이 전체 문서들 중 얼마나 희귀한지를 의미한다.

여러 테이블 설명에 공통으로 자주 나오는 표현은 중요도가 낮아진다.
반대로 특정 테이블에만 주로 등장하는 표현은 중요도가 높아진다.

즉, TF-IDF는 다음과 같은 표현에 높은 점수를 준다.

```text
한 문서 안에서는 자주 나오지만,
전체 문서에서는 흔하지 않은 표현
```

## 3. 이 프로젝트에서의 "문서"

일반적인 TF-IDF 검색에서는 문서가 게시글, 문서 파일, 웹페이지일 수 있다.
하지만 이 프로젝트에서는 테이블 하나가 하나의 문서처럼 취급된다.

관련 코드:

- `app/infra/db/schema_store.py`
- `config/schema/tc_oracle.yaml`

`SchemaStore.load()`는 설정 파일에 정의된 테이블명, 테이블 설명, 컬럼명, 컬럼 설명, glossary hint를 모아 하나의 문자열로 만든다.

개념적으로는 다음과 같다.

```python
doc = f"{table_name} {table_description} {column_names_and_descriptions}"
```

예를 들어 `TC_EQP_PARAM` 테이블이 있다면 내부 검색 문서는 대략 이런 형태가 된다.

```text
TC_EQP_PARAM 설비 파라미터 정보
LINEID 라인 ID
EQPID 설비 ID
SERVER_MODEL 서버 모델
PARAM_NAME 파라미터명
PARAM_VALUE 파라미터 값
```

테이블이 10개 있으면 TF-IDF 입장에서는 문서 10개가 있는 것과 같다.

## 4. 문자 n-gram 방식

이 프로젝트는 단어 단위가 아니라 문자 조각 단위로 TF-IDF 벡터를 만든다.

코드:

```python
TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
```

의미:

- `analyzer="char_wb"`: 단어 경계 안에서 문자 단위로 자른다.
- `ngram_range=(2, 4)`: 2글자, 3글자, 4글자 조각을 만든다.

예를 들어 `PARAMETER`라는 문자열은 다음과 같은 조각으로 나뉠 수 있다.

```text
2-gram: PA, AR, RA, AM, ME, ET, TE, ER
3-gram: PAR, ARA, RAM, AME, MET, ETE, TER
4-gram: PARA, ARAM, RAME, AMET, METE, ETER
```

이 방식은 단어가 완전히 일치하지 않아도 부분적으로 비슷한 문자열을 잡을 수 있다.
설비명, 파라미터명, 컬럼명처럼 코드성 문자열이 많은 DB 스키마 검색에 유용하다.

## 5. 검색 과정

질문이 들어오면 `SchemaStore.search()`가 다음 순서로 동작한다.

```python
q_vec = self._vectorizer.transform([query])
scores = cosine_similarity(q_vec, self._matrix).flatten()
top_idx = np.argsort(scores)[::-1][:top_k]
```

흐름:

1. 미리 테이블 설명들을 TF-IDF 벡터로 만들어 둔다.
2. 사용자 질문도 같은 방식으로 TF-IDF 벡터로 변환한다.
3. 질문 벡터와 각 테이블 벡터의 cosine similarity를 계산한다.
4. 점수가 높은 테이블 순서로 `top_k`개를 반환한다.

반환 결과는 대략 다음 형태다.

```python
{
    "table": "TC_EQP_PARAM",
    "score": 0.42,
    "config": {
        "description": "...",
        "columns": {...},
        "relationships": [...]
    }
}
```

## 6. Cosine Similarity

Cosine similarity는 두 벡터의 방향이 얼마나 비슷한지를 계산하는 방식이다.

점수 해석:

```text
1에 가까움: 매우 비슷함
0에 가까움: 관련성이 낮음
```

예를 들어 사용자 질문이 다음과 같다고 하자.

```text
A 설비의 PARAM_X 값 알려줘
```

`TC_EQP_PARAM` 테이블 설명에 `EQPID`, `PARAM_NAME`, `PARAM_VALUE` 같은 문자열이 있으면 질문과 문자 조각이 많이 겹친다.
그러면 해당 테이블의 cosine similarity 점수가 올라간다.

## 7. DB Agent 안에서의 역할

TF-IDF 검색은 DB Agent 파이프라인에서 가장 앞쪽에 위치한다.
LLM에게 전체 스키마를 모두 넘기기 전에 관련 있어 보이는 테이블 후보를 먼저 좁히는 역할이다.

전체 흐름:

```text
전체 스키마
  -> TF-IDF로 관련 테이블 top-k 검색
  -> SchemaLinker가 LLM으로 테이블/컬럼/JOIN 선택
  -> SQLGenerator가 좁혀진 schema_subset으로 SQL 생성
  -> Validator가 SQL 검증
  -> DB 실행
```

즉, TF-IDF는 최종 SQL을 직접 만들지는 않는다.
SQL 생성 전에 "LLM이 참고할 스키마 후보"를 줄이는 1차 검색기 역할을 한다.

## 8. 왜 필요한가?

LLM에게 전체 DB 스키마를 모두 주면 다음 문제가 생긴다.

- prompt가 길어진다.
- 비용이 늘어난다.
- 비슷한 테이블이 많을 때 잘못된 테이블을 고를 수 있다.
- 없는 컬럼을 만들어낼 가능성이 커진다.
- SQL 생성 품질이 불안정해진다.

TF-IDF 검색은 이런 문제를 줄이기 위해 관련 가능성이 높은 테이블만 먼저 추린다.

## 9. 장점

TF-IDF 기반 스키마 검색의 장점:

- 빠르다.
- 외부 embedding 서버가 필요 없다.
- 구현이 단순하다.
- 테스트와 재현이 쉽다.
- 테이블명, 컬럼명, 코드성 문자열 검색에 강하다.
- 운영 환경에서 비용이 거의 들지 않는다.

## 10. 한계

TF-IDF는 문자열 유사도 기반이기 때문에 의미 이해에는 한계가 있다.

주의할 점:

- 동의어를 잘 이해하지 못한다.
- 업무 용어가 스키마 설명에 없으면 못 찾을 수 있다.
- 축약어와 현업 표현이 매핑되지 않으면 점수가 낮을 수 있다.
- 깨진 한국어 설명은 검색 품질을 떨어뜨린다.
- 현재 구현은 threshold 없이 top-k를 반환하므로 관련 없는 후보도 포함될 수 있다.

예를 들어 사용자가 "장비"라고 묻는데 스키마 설명에는 "설비"만 있다면, 설명에 동의어 hint가 없을 경우 검색 점수가 기대보다 낮을 수 있다.

## 11. 개선 방향

운영 품질을 높이려면 다음 개선을 고려할 수 있다.

1. 스키마 설명과 glossary hint 보강
   - 현업 용어, 약어, 동의어를 컬럼 설명에 추가한다.

2. 깨진 한국어 텍스트 복구
   - `config/schema/*.yaml`
   - `config/prompts/*.j2`

3. 최소 score threshold 도입
   - 관련성이 너무 낮은 테이블은 prompt에서 제외한다.

4. 검색 결과 로그 추가
   - 질문별 top-k 테이블과 score를 기록해 품질을 분석한다.

5. Embedding 기반 검색 검토
   - 의미 기반 검색이 필요해지면 TF-IDF 대신 embedding 또는 hybrid search를 고려한다.

## 12. 한 줄 요약

이 프로젝트의 TF-IDF는 DB Agent가 SQL을 만들기 전에 관련 테이블 후보를 빠르게 좁히는 1차 스키마 검색기다.
문자 n-gram 기반이라 테이블명, 컬럼명, 파라미터명 같은 코드성 문자열에는 강하지만, 의미 기반 이해와 동의어 처리에는 한계가 있다.

---

# SchemaLinker 동작 설명

## 13. SchemaLinker란?

SchemaLinker는 TF-IDF로 검색된 스키마 후보를 바탕으로, 사용자 질문에 필요한 테이블, 컬럼, JOIN 조건을 LLM에게 고르게 하는 단계다.

관련 파일:

- `app/core/agents/db/schema_linker.py`
- `app/infra/db/schema_store.py`
- `config/prompts/schema_linker.j2`
- `app/core/agents/db/agent.py`

DB Agent 파이프라인에서 SchemaLinker는 SQL 생성보다 앞에 위치한다.

```text
User Question
  -> SchemaStore.search()
  -> SchemaLinker.link()
  -> schema_subset 구성
  -> SQLGenerator.generate()
```

즉, SchemaLinker는 SQL을 직접 만들지는 않는다.
SQL 생성 전에 "어떤 스키마를 보고 SQL을 만들 것인가"를 정하는 역할이다.

## 14. SchemaLinker의 입력과 출력

SchemaLinker의 입력은 사용자 질문이다.

```python
linked = await self.linker.link(question)
```

출력은 LLM이 반환한 JSON dict다.
의도된 구조는 다음과 같다.

```json
{
  "tables": ["테이블명"],
  "columns": ["테이블.컬럼명"],
  "joins": ["TABLE_A.col = TABLE_B.col"]
}
```

예를 들어 질문이 다음과 같다고 하자.

```text
A 설비의 PARAM_X 값 알려줘
```

기대되는 SchemaLinker 결과는 대략 다음과 같다.

```json
{
  "tables": ["TC_EQP_PARAM"],
  "columns": ["TC_EQP_PARAM.EQPID", "TC_EQP_PARAM.PARAM_NAME", "TC_EQP_PARAM.PARAM_VALUE"],
  "joins": []
}
```

## 15. 코드 기준 동작 흐름

핵심 구현은 `SchemaLinker.link()`에 있다.

```python
async def link(self, question: str) -> dict:
    results = self.schema_store.search(question, top_k=self.top_k)
    schema_context = self.schema_store.format_for_prompt(results)
    prompt = self.renderer.render(
        "schema_linker",
        schema_context=schema_context,
        question=question,
    )
    return await self.llm.complete_json(prompt)
```

순서:

1. `schema_store.search()`로 관련 테이블 후보를 검색한다.
2. `format_for_prompt()`로 후보 테이블 정보를 LLM prompt용 문자열로 바꾼다.
3. `schema_linker.j2` prompt를 렌더링한다.
4. LLM에게 JSON 형식 응답을 요청한다.
5. 결과를 dict로 반환한다.

## 16. SchemaStore와 SchemaLinker의 관계

SchemaStore는 검색을 담당하고, SchemaLinker는 선택을 담당한다.

```text
SchemaStore
  - 전체 스키마를 로드한다.
  - TF-IDF 인덱스를 만든다.
  - 질문과 유사한 테이블 후보 top-k를 반환한다.

SchemaLinker
  - 검색된 후보를 prompt로 만든다.
  - LLM에게 관련 테이블, 컬럼, JOIN을 고르게 한다.
  - JSON 결과를 반환한다.
```

둘의 역할을 구분하면 다음과 같다.

| 단계 | 담당 | 방식 | 결과 |
| --- | --- | --- | --- |
| 1차 후보 검색 | SchemaStore | TF-IDF 문자 n-gram | 관련 테이블 top-k |
| 2차 스키마 선택 | SchemaLinker | LLM JSON 응답 | tables, columns, joins |

## 17. DB Agent에서 SchemaLinker 결과를 사용하는 방식

DB Agent는 SchemaLinker 결과를 받은 뒤, 다시 SchemaStore 검색 결과와 결합해 SQL 생성용 `schema_subset`을 만든다.

관련 코드 개념:

```python
linked = await self.linker.link(question)
results = self.schema_store.search(question, top_k=5)
schema_subset = self.schema_store.format_for_prompt(
    [r for r in results if r["table"] in linked.get("tables", [])] or results[:3]
)
```

의미:

1. SchemaLinker가 선택한 테이블 목록을 가져온다.
2. TF-IDF 검색 결과 중에서 LLM이 선택한 테이블만 남긴다.
3. 선택된 테이블이 없으면 검색 상위 3개를 fallback으로 사용한다.
4. 최종 `schema_subset`을 SQLGenerator에 넘긴다.

이 구조 덕분에 LLM이 이상한 테이블명을 반환하더라도, 검색 결과와 교집합이 없으면 fallback이 동작한다.

## 18. SchemaLinker가 필요한 이유

TF-IDF만 사용하면 문자열 유사도 기준의 후보 검색까지만 가능하다.
하지만 실제 SQL 생성에는 다음 판단이 더 필요하다.

- 질문에 정말 필요한 테이블은 무엇인가?
- 어떤 컬럼을 SELECT해야 하는가?
- 어떤 컬럼을 WHERE 조건에 써야 하는가?
- 여러 테이블이 필요하면 JOIN 조건은 무엇인가?
- 검색 점수는 높지만 질문 의도와 다른 테이블은 제외해야 하는가?

SchemaLinker는 이 판단을 LLM에게 맡긴다.

즉:

```text
TF-IDF: "문자열상 비슷한 테이블 후보를 찾는다"
SchemaLinker: "질문 의도상 실제로 필요한 스키마를 고른다"
```

## 19. SQLGenerator와의 관계

SchemaLinker 결과는 SQLGenerator의 입력 품질을 높이는 데 사용된다.

SQLGenerator는 다음 정보를 바탕으로 SQL을 만든다.

- 사용자 질문
- SchemaLinker로 좁혀진 schema_subset
- Few-shot 예시
- 값 후보

SchemaLinker가 잘못된 테이블을 고르면 SQLGenerator도 잘못된 SQL을 만들 가능성이 커진다.
반대로 SchemaLinker가 정확하면 SQL 생성 prompt가 짧고 명확해진다.

## 20. 예시 시나리오

질문:

```text
A 설비의 PARAM_X 값 알려줘
```

동작 흐름:

1. SchemaStore가 질문과 유사한 테이블 후보를 찾는다.

```text
TC_EQP_PARAM
PARAMETER
TC_EQUIPMENT
```

2. SchemaLinker가 후보 스키마와 질문을 LLM에게 전달한다.

3. LLM이 필요한 테이블과 컬럼을 JSON으로 반환한다.

```json
{
  "tables": ["TC_EQP_PARAM"],
  "columns": [
    "TC_EQP_PARAM.EQPID",
    "TC_EQP_PARAM.PARAM_NAME",
    "TC_EQP_PARAM.PARAM_VALUE"
  ],
  "joins": []
}
```

4. DB Agent는 `TC_EQP_PARAM` 중심으로 `schema_subset`을 만든다.

5. SQLGenerator가 다음과 유사한 SQL을 생성할 수 있다.

```sql
SELECT PARAM_VALUE
FROM TC_EQP_PARAM
WHERE EQPID = 'EQP_A_001'
  AND PARAM_NAME = 'PARAM_X'
```

## 21. 현재 구현상 주의점

### 21-1. Prompt 인코딩 깨짐

현재 `config/prompts/schema_linker.j2`의 한국어 문구가 깨져 있다.
이 prompt는 LLM에게 직접 전달되므로 SchemaLinker 품질에 영향을 줄 수 있다.

정리 방향:

- UTF-8 기준으로 prompt 문구 복구
- DB dialect에 맞는 설명으로 재작성
- 출력 JSON schema를 더 명확히 지정

### 21-2. DB dialect 혼재

SchemaLinker prompt에는 Oracle 기준 문구가 남아 있다.
반면 SQLGenerator prompt에는 MySQL 8.0 규칙이 있다.

정리 방향:

- 실제 대상 DB를 MySQL 또는 Oracle 중 하나로 확정
- schema_linker, sql_gen, sql_refiner prompt의 DB 기준 통일

### 21-3. LLM 응답 검증 부족

현재 SchemaLinker는 LLM 응답을 그대로 반환한다.
다음 검증이 추가되면 안정성이 올라간다.

- `tables`가 list인지 확인
- 반환된 table이 실제 schema에 존재하는지 확인
- `columns`가 실제 whitelist에 있는지 확인
- `joins`에 존재하지 않는 테이블/컬럼이 들어가지 않았는지 확인

### 21-4. columns와 joins 활용이 약함

현재 DB Agent는 SchemaLinker 결과 중 주로 `tables`를 이용해 `schema_subset`을 좁힌다.
`columns`, `joins`는 SQLGenerator에 강하게 제약으로 전달되지는 않는다.

개선 방향:

- `linked["columns"]`를 SQLGenerator prompt에 명시적으로 전달
- `linked["joins"]`를 JOIN 후보로 별도 섹션에 표시
- Validator에서 linked column/table과 생성 SQL의 일치 여부를 점검

## 22. 개선된 SchemaLinker prompt 예시

현재 prompt를 복구한다면 다음처럼 명확하게 만드는 것이 좋다.

```jinja2
당신은 {{ dialect }} DB 스키마 분석가입니다.
사용자 질문에 답하기 위해 필요한 테이블, 컬럼, JOIN 조건만 선택하세요.

[사용 가능한 스키마 후보]
{{ schema_context }}

[사용자 질문]
{{ question }}

[규칙]
- 후보 스키마에 없는 테이블과 컬럼은 만들지 마세요.
- SELECT, WHERE, JOIN에 필요할 가능성이 높은 컬럼을 포함하세요.
- JOIN이 필요 없으면 joins는 빈 배열로 두세요.
- JSON만 출력하세요.

[출력 형식]
{
  "tables": ["TABLE_NAME"],
  "columns": ["TABLE_NAME.COLUMN_NAME"],
  "joins": ["TABLE_A.COL = TABLE_B.COL"],
  "rationale": "선택 이유 한 문장"
}
```

## 23. 한 줄 요약

SchemaLinker는 TF-IDF가 찾아온 스키마 후보를 LLM에게 보여주고, 질문에 필요한 테이블, 컬럼, JOIN 조건을 JSON으로 선택하게 하는 SQL 생성 전 단계다.
좋은 SchemaLinking은 SQLGenerator의 입력 범위를 좁혀 hallucination을 줄이고, 잘못된 테이블 선택 가능성을 낮춘다.
