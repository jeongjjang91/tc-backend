# Text-to-SQL Stage Classification Guide

- 작성일: 2026-04-24
- 목적: 현재 프로젝트의 실제 Text-to-SQL DB Agent 실행 구조를 기준으로 `T1~T20`을 어떤 단계로 분류해서 봐야 하는지 설명
- 참고 원문: [2026-04-21-text-to-sql-improvement-and-eval-system.md](/c:/project/backend/docs/superpowers/specs/2026-04-21-text-to-sql-improvement-and-eval-system.md)

## 1. 기준이 되는 실제 실행 구조

현재 코드 기준 Text-to-SQL 흐름은 아래처럼 이해하는 것이 가장 정확하다.

```text
Planner
  ↓
DB Agent 선택
  ↓
Schema Linker
  ↓
SchemaStore 재검색 / schema_subset 구성
  ↓
SQL Generator
  ↓
Validator
  ↓
DB Execution
  ↓
[조건부 Refiner 반복]
  ↓
Interpreter
  ↓
Synthesizer
  ↓
Final Answer / Eval Loop
```

중요한 해석:
- `Refiner`는 고정 단계가 아니라 검증 실패, 실행 오류, 빈 결과일 때만 도는 조건부 반복이다.
- 최종 사용자 응답은 `Interpreter`에서 끝나지 않고 상위 `Synthesizer`를 거쳐 완성된다.
- `T20`은 하나의 단계가 아니라 여러 단계를 가로지르는 공통 라우팅 인프라다.

## 2. 단계별로 T1~T20을 분류하면

| 단계 | 해당 T |
|------|--------|
| Planner | `T18`, `T19`, `T20` |
| Schema Linker | `T5`, `T11`, `T20` |
| SchemaStore 재검색 / schema_subset 구성 | `T5`, `T11` |
| SQL Generator | `T3`, `T4`, `T6`, `T8`, `T13`, `T14`, `T16`, `T20` |
| Validator | `T9`, `T17` |
| DB Execution | `T9`, `T12`, `T13`, `T17` |
| 조건부 Refiner | `T9`, `T17`, `T20` |
| Interpreter | `T20` |
| Synthesizer | 간접적으로 `T20` |
| Final Answer / Eval Loop | `T1`, `T2`, `T7`, `T10`, `T15` |

## 3. 왜 이렇게 분류하는가

### Planner

Planner는 사용자의 질문을 어떤 agent로 보낼지 결정하는 단계다.

- `T18`은 잡담/무효 입력을 미리 걸러내는 prefilter
- `T19`는 `prefilter + classifier + LLM fallback` 기반의 본분류
- `T20`은 planner fallback/decompose에 어떤 모델을 붙일지 정하는 라우팅

즉 Planner는 “질문을 어디로 보낼지”를 다루는 단계다.

### Schema Linker

Schema Linker는 질문과 관련된 테이블/컬럼을 좁혀 주는 단계다.

- `T5` schema description 품질 개선
- `T11` schema linker 2단계화
- `T20` schema_linking 모델 라우팅

즉 이 단계는 “무엇을 조회해야 하는지”를 결정한다.

### SQL Generator

SQL Generator는 실제 SQL을 만드는 단계다.

- `T3` ValueStore
- `T4` few-shot
- `T6` sql_gen prompt 개선
- `T8` self-consistency
- `T13` fuzzy value matching
- `T14` anti-pattern few-shot
- `T16` SQL generation ensemble
- `T20` sql_generation 모델 라우팅

즉 SQL 품질을 끌어올리는 대부분의 핵심 작업은 여기에 모여 있다.

### Validator / Refiner

Validator는 생성된 SQL이 안전하고 실행 가능한지 검사한다.  
Refiner는 실패한 경우에만 조건부로 SQL을 수정한다.

- `T9` execution-guided verification
- `T17` AST repair
- `T20` refine 라우팅

즉 “만든 SQL을 바로 쓰지 않고 다듬는 단계”다.

### DB Execution

검증된 SQL을 실제 DB에 실행하는 단계다.

- `T9`는 실행 결과를 다시 검증에 반영
- `T12`는 query log 기반 캐시
- `T13`은 실행 전 값 정합성 보강
- `T17`은 실행 전 SQL 구조 보정

즉 이 단계는 단순 실행처럼 보여도 캐시/검증과 밀접하게 연결된다.

### Interpreter / Synthesizer

Interpreter는 DB rows를 자연어 답변으로 바꾸고, Synthesizer는 그 결과를 포함한 최종 답변을 합성한다.

- `T20`의 interpretation routing/template 전략이 이 구간과 연결된다.

즉 사용자가 읽는 형태의 응답을 만드는 마지막 생성 단계다.

### Final Answer / Eval Loop

이 단계는 단일 함수보다 “최종 결과를 측정하고 운영 루프로 연결하는 영역”으로 보는 것이 맞다.

- `T1` eval DB 저장
- `T2` golden fixture
- `T7` baseline
- `T10` 평가 지표 확장
- `T15` active learning

즉 이 영역은 파이프라인 한 칸이 아니라 결과 품질을 관리하는 운영 축이다.

## 4. 가장 중요한 묶음만 다시 보면

### Planner 개선 묶음

- `T18`
- `T19`
- `T20`

### Schema Linker 개선 묶음

- `T5`
- `T11`
- `T20`

### SQL Generator 개선 묶음

- `T3`
- `T4`
- `T6`
- `T8`
- `T13`
- `T14`
- `T16`
- `T20`

### Validator / Refiner 개선 묶음

- `T9`
- `T17`
- `T20`

### Eval / 운영 루프 묶음

- `T1`
- `T2`
- `T7`
- `T10`
- `T15`

## 5. 실무적으로 해석할 때 주의할 점

1. `T20`은 어느 한 단계 전용 태스크가 아니다.
2. `T1`, `T2`, `T7`, `T10`, `T15`는 “마지막 단계”라기보다 전체 품질 관리 체계다.
3. `Refiner`는 SQL Generator 뒤에 항상 오는 단계가 아니라 실패 시에만 반복된다.
4. `Interpreter`와 `Final Answer`를 같은 것으로 보면 안 된다. 현재 구조에서는 `Synthesizer`가 최종 답변을 마무리한다.

## 6. 한 줄 요약

`T1~T20`은 기능별 목록으로 보면 흩어져 보이지만, 실제 실행 구조 기준으로 보면 `Planner`, `Schema Linker`, `SQL Generator`, `Validator/Refiner`, `DB Execution`, `Interpreter`, `Final Answer/Eval Loop`의 단계로 재분류해서 이해하는 것이 가장 정확하다.
