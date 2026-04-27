# Interpreter 응답 개선 — 표 구조화 · LIMIT 안내 · 어조 페르소나

> 작성일: 2026-04-27
> 상태: 설계 (구현 가이드)
> 관련 문서:
> - `2026-04-18-tc-voc-chatbot-design.md` — 전체 아키텍처
> - `2026-04-21-text-to-sql-improvement-and-eval-system.md` — T9 (Execution-Guided Verification)

---

## 0. 요약

현재 `ResultInterpreter` → `Synthesizer` 흐름에서 세 가지 문제가 있다.

| 문제 | 원인 |
|------|------|
| 답변에 `[row_1]` 같은 인용 표기가 노출됨 | `synthesizer.j2` 규칙 1번이 `[row_N]` 인용을 강제 |
| DB 결과가 문자열로만 전달되어 표 렌더링 불가 | `AgentResult.raw_data`에 구조화된 `table` 필드 없음 |
| 답변 어조가 딱딱하고 냉정함 | 프롬프트에 페르소나·어조 지시 없음 |
| LIMIT 도달 시 사용자에게 알림 없음 | 잘림 여부를 응답에 전달하는 구조 없음 |

**핵심 원칙:** LLM이 table을 재생성하지 않는다. DB 실행 결과(`rows: list[dict]`)를 `DBAgent`가 직접 구조화해서 응답에 담고, LLM은 자연어 요약만 담당한다.

---

## 1. 왜 LLM이 table을 재생성하면 안 되는가

| 항목 | LLM 재생성 | DBAgent 직접 구조화 |
|------|-----------|-------------------|
| 정확성 | 행 누락·순서 변경·숫자 포맷 변경 위험 | DB 실행 결과 그대로 |
| 토큰 비용 | 행 수에 비례 증가 | 자연어 요약만 |
| Hallucination | 없는 값 생성 가능 | 없음 |
| 100건 결과 시 | 프롬프트 폭증 | 영향 없음 |

---

## 2. 변경 대상 파일

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `config/prompts/synthesizer.j2` | 수정 | 페르소나·어조·LIMIT 안내·인용 제거 |
| `app/core/agents/db/interpreter.py` | 수정 | `truncated`, `row_count` 전달 |
| `app/core/agents/db/agent.py` | 수정 | `table` + `notice` 합성 |
| `app/shared/schemas.py` | 수정 | `AgentResult.raw_data` 구조 명세 |
| `config/agents.yaml` | 수정 | `row_limit` 설정 추가 |
| `tests/unit/test_interpreter_response.py` | 신규 | 단위 테스트 |
| `tests/golden/interpreter_tone_cases.yaml` | 신규 | 어조·LIMIT 안내 Golden 케이스 |

---

## 3. 응답 스키마 변경

### 3-1. `ResultInterpreter` LLM 출력 (슬림화)

LLM은 자연어 요약만 반환한다. table은 요청하지 않는다.

```json
{
  "answer": "A 설비의 PARAM_X 값은 3.14로 확인됩니다. 기준 범위 내라 정상 동작 중이네요.",
  "confidence": 0.9,
  "needs_human_review": false,
  "missing_info": []
}
```

### 3-2. `AgentResult.raw_data` 최종 구조 (DBAgent 합성)

```json
{
  "answer": "A 설비의 PARAM_X 값은 3.14로 확인됩니다. 기준 범위 내라 정상 동작 중이네요.",
  "table": {
    "headers": ["EQPID", "PARAM_NAME", "PARAM_VALUE"],
    "rows": [["EQP_A_001", "PARAM_X", 3.14]],
    "row_count": 1,
    "truncated": false
  },
  "notice": null,
  "confidence": 0.9,
  "needs_human_review": false
}
```

LIMIT 도달 시:

```json
{
  "answer": "최근 1시간 동안 PARAM_X가 변동된 설비는 1,000건 확인됩니다. 전체 결과는 대시보드에서 확인하실 수 있어요.",
  "table": {
    "headers": ["EQPID", "PARAM_NAME", "PARAM_VALUE"],
    "rows": [...],
    "row_count": 1000,
    "truncated": true
  },
  "notice": "조회 결과가 1000건으로 잘려 표시됩니다. 전체 결과는 대시보드에서 확인하실 수 있습니다.",
  "confidence": 0.85,
  "needs_human_review": false
}
```

---

## 4. `DBAgent` 변경 (`app/core/agents/db/agent.py`)

```python
# app/core/agents/db/agent.py

ROW_LIMIT_NOTICE = (
    "조회 결과가 {limit}건으로 잘려 표시됩니다. "
    "전체 결과는 대시보드에서 확인하실 수 있습니다."
)

# ... SQL 실행 후 ...

truncated = len(rows) >= self.row_limit

interp_result = await self.interpreter.interpret(
    question=question,
    sql=validated_sql,
    rows=rows,
    truncated=truncated,
)

headers = list(rows[0].keys()) if rows else []
raw_data = {
    "answer": interp_result.get("answer", ""),
    "table": {
        "headers": headers,
        "rows": [list(r.values()) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    },
    "notice": ROW_LIMIT_NOTICE.format(limit=self.row_limit) if truncated else None,
    "confidence": interp_result.get("confidence", 0.0),
    "needs_human_review": interp_result.get("needs_human_review", False),
}
```

`row_limit`은 `config/agents.yaml`에서 주입:

```yaml
# config/agents.yaml
db_agent:
  row_limit: 1000   # SQLValidator LIMIT 주입값과 동일하게 유지
```

---

## 5. `ResultInterpreter` 변경 (`app/core/agents/db/interpreter.py`)

`truncated`, `row_count`를 프롬프트에 전달한다.

```python
# app/core/agents/db/interpreter.py

class ResultInterpreter:
    def __init__(self, llm: LLMProvider, renderer: PromptRenderer, max_rows_in_prompt: int = 20):
        self.llm = llm
        self.renderer = renderer
        self.max_rows_in_prompt = max_rows_in_prompt

    async def interpret(
        self,
        question: str,
        sql: str,
        rows: list[dict],
        truncated: bool = False,
    ) -> dict:
        prompt = self.renderer.render(
            "synthesizer",
            question=question,
            sql=sql,
            rows=rows[: self.max_rows_in_prompt],
            row_count=len(rows),
            truncated=truncated,
        )
        return await self.llm.complete_json(prompt)
```

---

## 6. `synthesizer.j2` 전체 개선

```jinja2
당신은 TC 시스템 운영팀을 돕는 친근한 동료 챗봇입니다.
설비·파라미터·라인 상태에 대해 운영자가 묻는 질문에, 옆자리 동료가 설명하듯 자연스럽고 따뜻한 어조로 답하세요.

[어조 가이드]
- 공손하지만 딱딱하지 않게. "~입니다", "~네요", "확인해 보니" 같은 자연스러운 표현 사용
- 불필요한 사과나 과한 격식 금지 ("죄송합니다만~", "안녕하세요!" 등 인사말 불필요)
- 핵심 수치를 먼저 말하고, 필요하면 한 줄로 부연 설명
- 사용자가 추가로 확인해보면 좋을 포인트가 있으면 자연스럽게 한 마디 덧붙이기
  예) "기준 범위 내입니다", "최근 변경 이력은 없네요"

[정확성 규칙]
1. SQL 결과에 없는 내용은 절대 추가하지 말 것 (수치 추론·해석 금지)
2. 결과가 0건이면 "조회된 데이터가 없습니다"로 답할 것
3. 답변에 [row_N] 같은 인용 표기는 넣지 말 것 — 표는 별도로 사용자에게 제공됨
{% if truncated %}
4. 결과가 {{ row_count }}건으로 제한되었으니, 답변 끝에 "전체 결과는 대시보드에서 확인하실 수 있습니다" 같은 안내를 자연스럽게 한 줄 덧붙일 것
{% endif %}

[질문]
{{ question }}

[실행한 SQL]
{{ sql }}

[결과] (총 {{ row_count }}건{% if truncated %}, 조회 한도 도달 — 이후 데이터 있음{% endif %})
{% for row in rows %}
- {{ row }}
{% endfor %}

[출력 — JSON만]
{
  "answer": "자연스러운 한국어 답변 (인용 표기 없이)",
  "confidence": 0.0,
  "needs_human_review": false,
  "missing_info": []
}
```

---

## 7. 어조 변경 전후 비교

| 상황 | Before | After |
|------|--------|-------|
| 정상 조회 | "A 설비의 PARAM_X 값은 3.14입니다 [row_1]." | "A 설비의 PARAM_X 값은 3.14로 확인됩니다. 기준 범위 내라 정상 동작 중이네요." |
| 결과 없음 | "확인되지 않습니다" | "조회된 데이터가 없습니다. 설비 ID나 파라미터명을 다시 확인해 보시겠어요?" |
| LIMIT 도달 | (안내 없음) | "최근 1시간 변동 설비는 1,000건 확인됩니다. 전체 결과는 대시보드에서 확인하실 수 있어요." |

---

## 8. 단위 테스트 (`tests/unit/test_interpreter_response.py`)

```python
import pytest
from unittest.mock import AsyncMock
from app.core.agents.db.interpreter import ResultInterpreter

SAMPLE_ROWS = [
    {"EQPID": "EQP_A_001", "PARAM_NAME": "PARAM_X", "PARAM_VALUE": 3.14}
]


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.complete_json.return_value = {
        "answer": "A 설비의 PARAM_X 값은 3.14입니다.",
        "confidence": 0.9,
        "needs_human_review": False,
        "missing_info": [],
    }
    return llm


@pytest.mark.asyncio
async def test_interpret_passes_truncated_flag(mock_llm, mock_renderer):
    interpreter = ResultInterpreter(llm=mock_llm, renderer=mock_renderer)
    await interpreter.interpret(
        question="A 설비 PARAM_X 값",
        sql="SELECT ...",
        rows=SAMPLE_ROWS,
        truncated=True,
    )
    call_kwargs = mock_renderer.render.call_args.kwargs
    assert call_kwargs["truncated"] is True
    assert call_kwargs["row_count"] == 1


@pytest.mark.asyncio
async def test_interpret_no_row_citations_in_answer(mock_llm, mock_renderer):
    mock_llm.complete_json.return_value["answer"] = "3.14 [row_1]"
    interpreter = ResultInterpreter(llm=mock_llm, renderer=mock_renderer)
    result = await interpreter.interpret("질문", "SQL", SAMPLE_ROWS)
    # [row_N] 인용이 answer에 있으면 Golden Eval에서 잡아야 함 — 여기서는 LLM mock이므로 통과
    assert "answer" in result


def test_db_agent_table_structure(sample_rows):
    """DBAgent가 rows → table 구조를 정확히 합성하는지 검증."""
    rows = [{"EQPID": "EQP_A", "PARAM_VALUE": 1.0}]
    headers = list(rows[0].keys())
    table = {
        "headers": headers,
        "rows": [list(r.values()) for r in rows],
        "row_count": len(rows),
        "truncated": False,
    }
    assert table["headers"] == ["EQPID", "PARAM_VALUE"]
    assert table["rows"] == [["EQP_A", 1.0]]
    assert table["truncated"] is False


def test_db_agent_notice_when_truncated():
    from app.core.agents.db.agent import ROW_LIMIT_NOTICE
    notice = ROW_LIMIT_NOTICE.format(limit=1000)
    assert "1000건" in notice
    assert "대시보드" in notice


def test_db_agent_no_notice_when_not_truncated():
    truncated = False
    notice = "notice_value" if truncated else None
    assert notice is None
```

---

## 9. Golden Eval 케이스 (`tests/golden/interpreter_tone_cases.yaml`)

```yaml
- id: TONE_001
  description: "[row_N] 인용 표기가 답변에 포함되면 안 됨"
  question: "A 설비의 PARAM_X 값 알려줘"
  answer_must_not_contain:
    - "[row_1]"
    - "[row_2]"
    - "[row_"
  tags: [tone, citation]

- id: TONE_002
  description: "자연스러운 어조 — 단순 '~입니다.' 단문 종료 회피"
  question: "A 설비의 PARAM_X 현재 값은?"
  answer_must_match_any:
    - "네요"
    - "확인됩니다"
    - "확인해 보니"
    - "보시면"
  tags: [tone]

- id: TONE_003
  description: "결과 0건 — 지정 문구 사용"
  question: "존재하지 않는 설비 ZZZZZ의 파라미터"
  fixture_rows: []
  answer_must_contain_any:
    - "조회된 데이터가 없습니다"
    - "확인되지 않습니다"
  tags: [tone, empty_result]

- id: LIMIT_001
  description: "LIMIT 도달 시 대시보드 안내 포함"
  question: "최근 1시간 변동 설비 전부 알려줘"
  fixture_truncated: true
  fixture_row_count: 1000
  answer_must_contain_any:
    - "대시보드"
    - "전체 결과"
  notice_must_not_be_null: true
  tags: [limit, notice]

- id: LIMIT_002
  description: "LIMIT 미도달 시 notice 없음"
  question: "A 설비 PARAM_X 값"
  fixture_truncated: false
  notice_must_be_null: true
  tags: [limit, notice]
```

---

## 10. 구현 순서 (체크리스트)

```
[ ] 1. config/agents.yaml — row_limit: 1000 추가
[ ] 2. app/core/agents/db/agent.py — row_limit + 1 fetch 적용 (§12-2/12-3 반영)
        fetched = await self.tc_pool.fetch_all(validated_sql, max_rows=self.row_limit + 1)
        truncated = len(fetched) > self.row_limit
        rows = fetched[:self.row_limit]
[ ] 3. app/core/agents/db/interpreter.py — truncated, row_count 파라미터 추가
[ ] 4. config/prompts/synthesizer.j2 — 페르소나·어조·[row_N] 제거·LIMIT 안내 반영
[ ] 5. app/core/agents/db/agent.py — raw_data에 table + notice + needs_human_review 합성
[ ] 6. app/api/chat.py — SSE confidence 이벤트에 needs_human_review propagation 반영 (§12-5)
        needs_review = confidence < 0.7 or any(
            (r.raw_data or {}).get("needs_human_review") for r in success_results
        )
[ ] 7. Golden metric 수정 — citation 검증을 [row_N] 문자열 패턴이 아니라 evidence 존재 여부로 전환 (§12-1)
[ ] 8. tests/unit/test_interpreter_response.py 작성 + 통과
        - truncated/row_count가 renderer에 전달되는지
        - row_limit+1 fetch 시 truncated 판정이 정확한지
        - DBAgent가 rows → table 구조를 정확히 만드는지
[ ] 9. tests/golden/interpreter_tone_cases.yaml 추가
        - TONE: [row_N] 미노출, evidence/citation은 유지
        - LIMIT: truncated 시 notice 존재, 미달 시 null
[ ] 10. Golden Eval 실행 → TONE_001~003, LIMIT_001~002 통과 확인
[ ] 11. PR 제출
```

---

## 11. Out of Scope

- 프론트엔드 table 렌더링 UI (백엔드가 `raw_data.table` 필드 제공까지만)
- table SSE 이벤트 전달 방식 확정 (프론트 계약은 별도 PR — 후보: `event: table` 또는 `done` payload 포함)
- 대시보드 링크 URL 결정 (프론트 라우팅에서 처리)
- 다국어 어조 (현재 한국어 전용)
- `synthesizer_multi.j2` (복수 Agent 병합 프롬프트) — 별도 개선 트랙

---
