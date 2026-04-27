---
name: llm-benchmark
description: Use when evaluating multi-LLM performance (response latency, answer quality, stage-level breakdown, mixed model configurations) in this project. Triggered by requests like "모델 성능 평가", "응답속도 비교", "LLM 벤치마크", "혼용 구성 비교", "단계별 레이턴시", "routing 평가", or when preparing PPT benchmark results. Always outputs MD report + structured log.
---

# LLM Benchmark — 응답속도 · 답변 품질 · 단계별 혼용 구성 평가

## Overview

이 프로젝트의 LLM 구성을 **named config 단위**로 평가한다. 단계마다 다른 모델을 혼용하는 실제 `llm_routing.yaml` 구성을 그대로 벤치마크할 수 있다.

**핵심 원칙:** 모든 평가 결과는 `docs/benchmark/YYYY-MM-DD-{label}.md` + `.json`으로 반드시 저장한다. 저장하지 않으면 평가로 인정하지 않는다.

---

## 평가 단위 — Named Config

모델을 전체 단위(`gpt-oss-only`)로 고정하거나, 단계마다 다른 모델을 혼용하는 구성(`beta-routing`)을 이름 붙여 비교한다.

### Config 정의 (`config/benchmark_configs.yaml`)

```yaml
configs:
  gpt-oss-only:
    planner:      gpt-oss
    linker:       gpt-oss
    generator:    gpt-oss
    refiner:      gpt-oss
    interpreter:  gpt-oss

  beta-routing:                  # llm_routing.yaml β안과 동일
    planner:      rule            # rule-based (LLM 미호출)
    linker:       gemma3
    generator:    ensemble        # GPT-OSS ∥ Gemma3 병렬
    refiner:      gpt-oss
    interpreter:  template        # LLM 미호출

  gemma3-light:
    planner:      rule
    linker:       gemma3
    generator:    gemma3
    refiner:      gemma3
    interpreter:  template

  planner-llm:                   # Planner LLM 효과 측정용
    planner:      gpt-oss         # LLM mode
    linker:       gemma3
    generator:    ensemble
    refiner:      gpt-oss
    interpreter:  template
```

**특수 값:**
- `rule` — LLM 미호출, rule-based 분기 (`planner_ms ≈ 0`)
- `template` — LLM 미호출, 템플릿 렌더링 (`interpreter_ms ≈ 0`)
- `ensemble` — GPT-OSS ∥ Gemma3 병렬 호출 (`EnsembleLLM`)

---

## LLM 호출 단계 구조

요청 1건당 LLM이 호출되는 단계와 모델 배정:

```
사용자 질문
 │
 ├─ [Planner] QueryPlanner.plan_async()
 │    └─ LLM 여부: 조건부 (llm 주입 시 GPT-OSS, 미주입 시 rule-based)
 │    └─ 측정: planner_ms, planner_mode (llm | rule)
 │
 └─ [DBAgent]
      ├─ [1] SchemaLinker.link()         → Gemma3 (경량)
      │    └─ 측정: linker_ms
      │
      ├─ [2] SQLGenerator.generate()     → GPT-OSS or Ensemble
      │    └─ 측정: generator_ms, model (gpt-oss | gemma3 | ensemble)
      │
      ├─ [3] SQLRefiner.refine()         → GPT-OSS (조건부, 실패 시)
      │    └─ 측정: refiner_ms, refine_count (0~max_refine)
      │
      └─ [4] ResultInterpreter.interpret() → Template or Gemma3
           └─ 측정: interpreter_ms, interpreter_mode (template | llm)
```

---

## 측정 항목

### 1. 단계별 레이턴시

| 지표 | 설명 | 단계 |
|------|------|------|
| `planner_ms` | Planner LLM 호출 시간 (rule이면 ~0ms) | Planner |
| `planner_mode` | `llm` or `rule` | Planner |
| `linker_ms` | SchemaLinker LLM 호출 시간 | DBAgent-1 |
| `generator_ms` | SQLGenerator LLM 호출 시간 | DBAgent-2 |
| `refiner_ms` | SQLRefiner LLM 호출 시간 (총합) | DBAgent-3 |
| `refine_count` | Refiner 실제 호출 횟수 (0~2) | DBAgent-3 |
| `interpreter_ms` | ResultInterpreter 호출 시간 | DBAgent-4 |
| `interpreter_mode` | `template` or `llm` | DBAgent-4 |
| `db_exec_ms` | TC DB 실행 시간 | DBAgent |
| `total_ms` | 전체 wall-clock | 전체 |
| `llm_call_count` | 실제 LLM 호출 수 (rule/template 제외) | 전체 |

### 2. 전체 레이턴시

| 지표 | 설명 |
|------|------|
| `p50 / p90 / p99` | 백분위 레이턴시 (`--repeat N` 기준) |
| `ttft_ms` | Time to First Token (SSE 기준) |

### 3. 답변 품질

| 지표 | 설명 | 측정 방법 |
|------|------|----------|
| `ex_score` | Execution Match — SQL 실행 결과 일치율 | Golden Dataset |
| `valid_sql_rate` | SQLValidator 통과율 | SQLValidator |
| `schema_recall_at5` | SchemaLinker top-5 정확도 | Golden Dataset |
| `confidence_mean` | LLM self-reported 신뢰도 평균 | 실행 로그 |
| `needs_review_rate` | human review 필요 비율 | 실행 로그 |

### 4. 앙상블 전용

| 지표 | 설명 |
|------|------|
| `agreement_rate` | GPT-OSS ↔ Gemma3 실행 결과 일치율 |
| `al_queue_rate` | 불일치로 Active Learning 큐 적재 비율 |
| `wall_clock_vs_single` | 단독 GPT-OSS 대비 wall-clock 증가율 |

---

## 출력 파일 규칙

### MD 리포트 (`docs/benchmark/YYYY-MM-DD-{label}.md`)

PPT 슬라이드 1장 = MD 섹션 1개.

```markdown
# LLM 벤치마크 — {label}
> 실행일: YYYY-MM-DD HH:MM | Git SHA: {sha} | 케이스 수: N | 반복: R회

## 요약
| Config | Planner | Linker | Generator | Interpreter | EX Score | P50 (ms) | LLM 호출수 |
|--------|---------|--------|-----------|-------------|----------|----------|----------|
| gpt-oss-only | gpt-oss | gpt-oss | gpt-oss | gpt-oss | 0.00 | 0 | 4 |
| beta-routing | rule | gemma3 | ensemble | template | 0.00 | 0 | 2~3 |
| gemma3-light | rule | gemma3 | gemma3 | template | 0.00 | 0 | 2 |

## 단계별 레이턴시 (P50, ms)
| 단계 | gpt-oss-only | beta-routing | gemma3-light |
|------|-------------|-------------|-------------|
| Planner | 0 | 0 (rule) | 0 (rule) |
| SchemaLinker | 0 | 0 | 0 |
| SQLGenerator | 0 | 0 | 0 |
| SQLRefiner (avg) | 0 | 0 | 0 |
| ResultInterpreter | 0 | 0 (template) | 0 (template) |
| DB 실행 | 0 | 0 | 0 |
| **합계** | **0** | **0** | **0** |

## Planner 분석
- Config별 LLM mode vs rule mode 비율
- LLM mode P50: Xms / rule mode: ~0ms

## 앙상블 분석 (beta-routing 전용)
- Agreement rate: X%
- AL queue 적재율: Y%
- Wall-clock 증가: +Z% vs gpt-oss-only

## 케이스별 결과
| ID | 질문 | Planner | Refine 횟수 | EX | Total (ms) |
|----|------|---------|------------|-----|-----------|

## 회귀 경보
baseline 대비 -5% 초과 항목 목록 (없으면 "없음")

## 결론 및 권장사항
한 줄 요약 + 병목 단계 + 다음 액션
```

### JSON 로그 (`docs/benchmark/YYYY-MM-DD-{label}.json`)

```json
{
  "meta": {
    "date": "YYYY-MM-DD",
    "label": "...",
    "git_sha": "...",
    "golden_dataset": "...",
    "n_cases": 0,
    "repeat": 1
  },
  "configs": {
    "beta-routing": {
      "routing": {
        "planner": "rule", "linker": "gemma3",
        "generator": "ensemble", "refiner": "gpt-oss", "interpreter": "template"
      },
      "ex_score": 0.0, "valid_sql_rate": 0.0,
      "p50_ms": 0, "p90_ms": 0,
      "llm_call_count_mean": 0,
      "stages": {
        "planner_ms": 0, "planner_mode": "rule",
        "linker_ms": 0, "generator_ms": 0,
        "refiner_ms": 0, "refine_count_mean": 0,
        "interpreter_ms": 0, "interpreter_mode": "template",
        "db_exec_ms": 0
      },
      "ensemble": { "agreement_rate": 0.0, "al_queue_rate": 0.0 }
    },
    "gpt-oss-only": { "...": "동일 구조" },
    "gemma3-light":  { "...": "동일 구조" }
  },
  "cases": [
    {
      "id": "...",
      "question": "...",
      "results": {
        "beta-routing": {
          "sql": "...", "ex_match": true, "total_ms": 0,
          "stages": { "planner_ms": 0, "linker_ms": 0, "generator_ms": 0,
                      "refine_count": 0, "interpreter_ms": 0, "db_exec_ms": 0 },
          "ensemble": { "agreement": true }
        },
        "gpt-oss-only": {
          "sql": "...", "ex_match": true, "total_ms": 0,
          "stages": { "..." : "동일" }
        }
      }
    }
  ],
  "regression": {
    "baseline_file": "...",
    "alerts": []
  }
}
```

---

## 실행 절차

```
1. Golden Dataset 확인
   └─ tests/golden/datasets/db_phase1.yaml (기본)
   └─ --dataset 옵션으로 교체 가능

2. 모델별 실행 (각 케이스를 지정 모델로)
   └─ time.perf_counter()로 각 단계 래핑
   └─ Planner: llm 주입 여부에 따라 planner_mode 기록
   └─ Refiner: 호출 횟수(refine_count) 누적

3. 레이턴시 집계
   └─ repeat N회 결과로 p50 / p90 계산
   └─ 단계별 평균도 함께 기록

4. 회귀 판정
   └─ 이전 benchmark JSON 로드 → ex_score 비교
   └─ baseline 대비 -5% 초과 시 alerts 배열에 추가

5. 파일 저장
   └─ docs/benchmark/ 디렉토리 없으면 생성
   └─ MD + JSON 동시 저장
   └─ git add docs/benchmark/ 안내 출력
```

---

## 스크립트 위치

```
scripts/
  benchmark_llm.py     # 메인 실행 스크립트
  benchmark_report.py  # JSON → MD 변환 (별도 실행 가능)
```

실행 예:

```bash
# named config 비교 (기본)
python scripts/benchmark_llm.py \
  --configs gpt-oss-only beta-routing gemma3-light \
  --dataset tests/golden/datasets/db_phase1.yaml \
  --label "phase1-routing-compare" \
  --repeat 3

# llm_routing.yaml을 config로 직접 사용
python scripts/benchmark_llm.py \
  --routing-yaml config/llm_routing.yaml \
  --label "current-routing" \
  --repeat 3

# baseline 저장
python scripts/benchmark_llm.py \
  --configs gpt-oss-only \
  --label "baseline" \
  --set-baseline
```

---

## PPT 변환 가이드

| MD 섹션 | PPT 슬라이드 |
|---------|------------|
| 요약 표 | Config별 핵심 지표 비교 (EX Score, P50, LLM 호출수) |
| 단계별 레이턴시 | Config별 스택 막대 그래프 — 어느 단계가 병목인지 시각화 |
| Planner 분석 | LLM vs Rule 레이턴시/비율 비교 |
| 앙상블 분석 | Agreement Rate / AL Queue (ensemble 포함 config만) |
| 품질 상세 | EX Score / Valid SQL Rate config별 비교 |
| 회귀 경보 | 품질 게이트 통과 여부 |
| 결론 | 최적 config 권장 + 다음 액션 |

그래프 데이터는 JSON의 `configs[*].stages` 블록에서 추출해 Excel / Chart.js 등으로 시각화한다.

---

## 자주 하는 실수

| 실수 | 올바른 방법 |
|------|-----------|
| `--models` 로 단일 모델 지정 | `--configs`로 named config 단위 지정 |
| 모든 단계에 같은 모델 고정 | `config/benchmark_configs.yaml`에 단계별 모델 명시 |
| 전체 wall-clock만 측정 | 단계별(`linker_ms`, `generator_ms` 등) 필수 기록 |
| `rule` / `template`을 LLM 시간으로 계상 | `planner_mode`, `interpreter_mode` 구분 후 0ms 처리 |
| Refiner 미호출을 측정에서 제외 | `refine_count=0`으로 기록 (케이스 집계 포함) |
| 파일 저장 없이 터미널 출력만 | 반드시 MD + JSON 저장 |
| 단일 실행으로 p90 계산 | `--repeat 3` 이상 |
| baseline 없이 회귀 판정 | 첫 실행 시 `--set-baseline` 플래그로 저장 |
| eval_case 테이블 미기록 | git_sha + 점수 반드시 DB 저장 (T1 인프라) |
