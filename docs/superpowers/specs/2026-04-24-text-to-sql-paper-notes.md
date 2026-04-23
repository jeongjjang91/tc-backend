# Text-to-SQL Paper Notes

- 작성일: 2026-04-24
- 목적: 현재 Text-to-SQL 개선 방향을 논문화할 수 있는지, 가능하다면 어떤 포지션과 실험 설계로 가져가야 하는지 정리한 메모

## 1. 결론 먼저

Text-to-SQL만으로도 논문 한 편은 충분히 가능하다.  
다만 단순 기능 개선 기록으로는 어렵고, 아래 세 가지가 필요하다.

1. 명확한 연구 질문
2. 재현 가능한 실험 설계
3. 의미 있는 baseline / ablation / 평가 지표

즉, "우리 서비스가 좋아졌다"가 아니라  
"이런 조건의 Text-to-SQL에서는 이런 구조가 유의미하다"를 주장해야 한다.

## 2. 어떤 논문 포지션이 현실적인가

현재 프로젝트 성격을 보면 순수 알고리즘 논문보다는 아래 포지션이 더 현실적이다.

### 2-1. 운영형 Text-to-SQL 시스템 논문

핵심 주장 예시:

- 운영형 Text-to-SQL의 핵심 문제는 단순 SQL 생성이 아니라 planner, schema linking, value grounding, verification, evaluation이다.
- 이를 위한 multi-stage architecture가 정확도와 latency를 함께 개선한다.

### 2-2. 평가 체계 논문

핵심 주장 예시:

- 기존 Text-to-SQL 평가는 execution accuracy 중심이라 실제 운영 품질을 충분히 설명하지 못한다.
- execution accuracy 외에 valid SQL, component match, latency, fallback rate, human review rate를 함께 보는 평가 프레임워크가 필요하다.

### 2-3. 도메인 특화 Text-to-SQL 논문

핵심 주장 예시:

- 반도체/설비/산업 도메인에서는 schema ambiguity와 value normalization이 공개 벤치마크와 다른 핵심 병목이다.
- 이를 해결하기 위한 실용적 구조가 유효하다.

## 3. 기본 구조는 무엇으로 잡을 것인가

논문에서 "개선되었다"를 쓰려면 baseline이 필요하다.  
가장 무난한 기본 구조는 아래 둘 중 하나다.

### Baseline A. Direct LLM Text-to-SQL

```text
Question + Full Schema
  -> LLM
  -> SQL
  -> Execute
```

설명:
- 가장 단순하고 널리 알려진 LLM 기반 Text-to-SQL baseline
- 구현은 쉽지만 schema가 커질수록 품질이 흔들림

### Baseline B. Basic production-style pipeline

```text
Question
  -> Schema Linking
  -> SQL Generation
  -> Execute / Repair
```

설명:
- 운영형 구조에 더 가까운 baseline
- 현재 시스템과 비교하기 좋은 stronger baseline

## 4. 현재 시스템은 baseline 대비 무엇이 추가되었는가

기본 구조:

```text
Question
  -> Schema
  -> LLM SQL generation
  -> Execute
  -> Answer
```

현재 개선 구조:

```text
Question
  -> Planner
  -> Schema Linker
  -> Schema subset construction
  -> SQL Generator
  -> Validator
  -> DB Execution
  -> Conditional Refiner
  -> Interpreter
  -> Synthesizer
  -> Eval / Feedback Loop
```

즉, 개선 포인트는 다음과 같이 설명할 수 있다.

- planner 기반 진입 통제
- schema narrowing
- few-shot 및 value grounding
- fuzzy matching
- execution-guided verification
- conditional refinement
- 운영형 평가 및 active learning loop
- multi-model routing

## 5. 논문감이 있는 세부 기여 포인트

현재 문서 기준으로 특히 논문화 가능성이 있는 축은 아래와 같다.

### 5-1. Planner 구조

- `prefilter + classifier + LLM fallback` 구조
- 좁은 4-class 분류 + mixed intent decomposition
- latency와 비용 절감

### 5-2. Schema linking 강화

- schema description 품질 개선
- 2-stage schema linker
- schema subset narrowing

### 5-3. SQL generation grounding

- ValueStore
- few-shot expansion
- anti-pattern prompt
- fuzzy value matching

### 5-4. Verification / refinement

- execution-guided verification
- AST repair
- refine loop

### 5-5. 운영형 평가 체계

- eval_run / eval_case 저장
- EX, Valid SQL, Component Match
- latency, fallback rate
- active learning queue

## 6. 무엇이 있어야 실제 논문이 되는가

### 6-1. Research Question

예시:

- 운영형 Text-to-SQL에서 정확도와 latency를 동시에 개선할 수 있는가?
- schema linking, value grounding, verification이 direct generation baseline 대비 얼마나 기여하는가?
- execution accuracy만으로 운영 품질을 설명할 수 있는가?

### 6-2. Strong baseline

최소 2개 정도는 두는 것이 좋다.

- Direct generation baseline
- basic production pipeline baseline

### 6-3. Ablation Study

구성 요소를 하나씩 켰을 때 얼마나 좋아지는지 보여줘야 한다.

예시:

- + ValueStore (`T3`)
- + Few-shot (`T4`)
- + Schema description (`T5`)
- + SQL prompt 개선 (`T6`)
- + Schema linker 2-stage (`T11`)
- + Fuzzy matching (`T13`)
- + Anti-pattern (`T14`)
- + Verification (`T9`)
- + Planner 3-tier (`T19`)
- + Model routing (`T20`)

### 6-4. Multi-metric evaluation

적어도 아래는 함께 보는 것이 좋다.

- Execution Accuracy
- Valid SQL Rate
- Component Match
- Hard subset accuracy
- Latency
- Fallback rate
- Human review rate

### 6-5. Error analysis

아래를 정리해야 논문 설득력이 생긴다.

- 어떤 유형에서 실패하는가
- 개선 후 어떤 실패 유형이 줄었는가
- mixed intent / schema ambiguity / value mismatch 중 어디가 가장 큰 병목이었는가

## 7. 논문이 안 되는 경우

아래 조건이면 논문화가 어려워진다.

- 개선점이 너무 많아서 어떤 것이 효과였는지 설명 못함
- train / dev / eval 분리 없이 결과를 제시함
- baseline이 약함
- 단순히 LLM 모델만 바꿔서 좋아졌다고 주장함
- 숫자 개선이 작고 불안정함
- 산업 시스템 구현 기록에만 머무름

## 8. 추천 논문 포지션

가장 현실적인 포지션은 아래다.

### 추천 포지션

Production-oriented / domain-specific Text-to-SQL system paper

### 이유

- 현재 구조는 생성 자체보다 운영성, 검증, 라우팅, 평가 체계가 강점이다.
- 순수 알고리즘 novelty보다는 system design novelty가 더 잘 드러난다.
- 산업 도메인 특화 문제를 설명하기 좋다.

## 9. 추천 제목 느낌

예시:

- Toward Production-Ready Text-to-SQL for Industrial Domain QA
- A Multi-Stage Architecture for Reliable Text-to-SQL in Enterprise Environments
- Beyond Execution Accuracy: Evaluating Production Text-to-SQL Systems with Reliability and Latency Metrics

## 10. 논문 서술 예시

### 기본 구조 설명 문장

> A standard LLM-based Text-to-SQL pipeline takes a natural language question, serializes the database schema into a prompt, generates SQL with an LLM, and executes the generated query.

### 개선 구조 설명 문장

> Our system extends this baseline with explicit planning, schema narrowing, value grounding, execution-guided refinement, and production-oriented evaluation and routing components.

## 11. 지금 프로젝트 기준 추천 실험 순서

1. Direct generation baseline 구축
2. Basic production baseline 구축
3. 현재 개선 구조 적용
4. ablation 순차 실행
5. latency / accuracy / fallback / valid SQL 측정
6. 실패 사례 정리

## 12. 실무 관점 최종 판단

정리하면:

- Text-to-SQL만으로도 논문은 가능하다.
- 가장 가능성이 높은 형태는 운영형 / 도메인형 / 평가형 논문이다.
- 핵심은 "기능 추가"가 아니라 "구조적 기여 + 측정 가능한 개선"으로 바꾸는 것이다.

한 줄 결론:

**이 프로젝트는 단순 Text-to-SQL 기능 구현으로는 약하지만, 운영형 multi-stage Text-to-SQL architecture와 evaluation framework로 잡으면 논문화 가능성이 충분하다.**
