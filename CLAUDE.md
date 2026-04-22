# TC VOC Chatbot — Backend

## 프로젝트 한 줄
TC 시스템 운영팀 VOC를 사내 오픈소스 LLM + Text-to-SQL/RAG/Splunk로 자동·반자동 응답하는 백엔드.

**VOC 4유형:** (1) 설비 기능 조회 · (2) 설비 비교 → **Text-to-SQL(자동)** / (3) 오동작 원인 → **Splunk(반자동)** / (4) 기능 설명 → **RAG(자동)**.

## 기술 스택
- **Backend:** FastAPI (Python, async)
- **App DB / TC DB:** MySQL 8.0 (TC DB는 read-only). **Redis/Kafka 등 추가 인프라 금지.** `DBPool` ABC로 Oracle 이전 여지 유지.
- **LLM:** 사내 오픈소스 (GPT-OSS / Gemma4). 파인튜닝 불가. **ModelRouter로 task별 경량/대형 분리** (Planner·Schema Linking·Interpretation → 경량, SQL 생성·Refine → 대형).
- **외부 API:** 사내 RAG(Confluence), Splunk.

## 개발 우선순위
Phase 1(DB Agent) → 2(RAG) → 3(Splunk) → 4(Knowledge + 품질 자동화).
**Walking Skeleton + Vertical Slice** — 경계(Agent I/F, LLM Provider, 프롬프트 분리, SSE, trace_id)는 Phase 1에 선확정.

## 디렉토리 구조

```
app/
├── api/         # HTTP (라우터, 미들웨어, SSE)
├── core/        # 도메인 (FastAPI/DB import 금지, 단위 테스트 가능)
│   ├── orchestrator/   # QueryPlanner — prefilter / rule / 경량 LLM 3단 분류
│   ├── agents/         # Agent ABC + 구현체 (plugin + AGENT_REGISTRY)
│   └── synthesizer.py
├── infra/       # 외부 의존성 어댑터
│   ├── llm/     # Provider ABC + ModelRouter
│   ├── db/      # MySQL (DBPool ABC)
│   ├── rag/     # 사내 RAG + Reranker
│   └── splunk/
└── shared/      # Pydantic 모델, 예외, 로깅
config/          # prompts/*.j2, schema/*.yaml, whitelist.yaml, llm_routing.yaml
tests/{unit,integration,component,golden}
docs/superpowers/specs/   # 설계 문서
```

## 코드 작성 규칙 (필수)
- **프롬프트는 `config/prompts/*.j2`에만.** 코드 내 하드코딩 금지.
- **Agent는 ABC 상속 + `AGENT_REGISTRY` 등록 + `config/agents.yaml` 기재.**
- **`core/`는 FastAPI·DB import 금지.** 외부 의존성은 `infra/` 어댑터로 주입.
- **한 파일 한 책임.** SchemaLinker / SQLGenerator / Validator / Verification / ASTRepair 등 단계별로 파일 분리.
- **모든 LLM 호출에 `trace_id` 전파.** 디버깅·평가 기반.
- **ModelRouter 사용.** Agent 내부에서 Provider 직접 생성 금지 → `router.get("task_name")`으로 주입.
- **설정 3계층 준수:**
  - **Code** — 클래스·핵심 로직 (PR 리뷰 + 배포)
  - **YAML** — 프롬프트, 스키마, 시드 패턴, **화이트리스트(보안 임계 → DB 저장 금지)**
  - **DB** — Few-shot 누적, Knowledge, 임계값, Feature Flag (운영 핫리로드 가능)
- **핫리로드:** Pub/Sub 없이 `config_version` 테이블 30초 폴링.

## LLM 시스템 원칙 (매우 중요)
1. **정확 출력 일치 검증 금지.** 속성/계약 검증만 — JSON 스키마, 필드 존재, 실행 결과 동치성, 키워드 포함, 신뢰도 범위.
2. **프롬프트 변경은 Golden Eval 필수 통과.** baseline -5% 초과 회귀 시 머지 차단. 프롬프트는 코드와 동급 자산.
3. **Golden Dataset은 Day 1부터 Git 커밋.** 👎 피드백·검토자 수정 답변은 Golden에 추가. baseline은 내리지 않고 올리기만.

## 보안 원칙
- TC DB는 **read-only 커넥션 전용**. SQL은 **SELECT만**, 화이트리스트(`config/whitelist.yaml`) 테이블/컬럼만 접근.
- 모든 SQL **LIMIT 자동 주입**, 대형 테이블은 **WHERE 강제**.
- **Prompt injection 방어:** 사용자 입력과 시스템 프롬프트 명확히 분리.
- LLM/DB 자격증명은 **환경변수/Secret Manager**. 코드·설정 파일에 절대 금지.

## 품질 게이트

| 단계 | 실행 | 기준 |
|------|------|------|
| Pre-commit | unit + integration | 100% pass |
| PR | + component(실 LLM) + Golden Eval | baseline -5% 회귀 금지 |
| Nightly | Golden Full | 트렌드 자동 리포트 |
| Pre-release | + 수동 E2E smoke | 핵심 5~10 시나리오 통과 |

**평가 자동 기록:** `eval_run` / `eval_case` 테이블에 git_sha·점수·실행 시간 저장 → Ablation Study·논문 데이터 근거.

## 참고 설계 문서
- `docs/superpowers/specs/2026-04-18-tc-voc-chatbot-design.md` — 전체 아키텍처
- `docs/superpowers/specs/2026-04-20-dashboard-table-viewer-design.md` — 테이블 뷰어
- `docs/superpowers/specs/2026-04-21-text-to-sql-improvement-and-eval-system.md` — T1~T20 개선 로드맵 (평가 자동화, Self-Consistency, ModelRouter, Planner 개선)
- `docs/AGENT_GUIDE.md` — 사내 AI Agent 온보딩 가이드

## 자주 하는 작업
- **새 Agent:** `core/agents/` 구현 + `AGENT_REGISTRY` 등록 + `config/agents.yaml` + Golden 시나리오 추가.
- **프롬프트 튜닝:** `config/prompts/*.j2` 수정 → Golden Eval 통과 → PR.
- **Few-shot 추가:** 운영자 UI 또는 DB INSERT (배포 불필요).
- **화이트리스트 변경:** `config/whitelist.yaml` + PR (**보안 리뷰 필수**).
- **모델 라우팅 변경:** `config/llm_routing.yaml` 수정 → Golden Eval로 task별 모델 적합성 확인.
