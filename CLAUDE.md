# TC VOC Chatbot — Backend

## 프로젝트 한 줄

TC 시스템 운영팀이 받는 VOC를 사내 오픈소스 LLM(GPT-OSS, Gemma4) + RAG/Text-to-SQL/Splunk 분석으로 자동/반자동 응답하는 백엔드.

## 기술 스택

- **Backend:** FastAPI (Python, async)
- **Frontend:** Vue.js + Vite (별도 레포, SSE 챗봇 UI)
- **App DB:** Oracle (운영/세션/지식 일체. **Redis 등 추가 인프라 사용 금지** — 운영 부담 최소화)
- **데이터 소스:** Oracle TC DB(MODEL/PARAMETER/DCOL_ITEM 등), 사내 RAG API(Confluence), Splunk API
- **LLM:** 사내 오픈소스 LLM API (파인튜닝 불가)

## VOC 4개 유형

| # | 유형 | 처리 | 자동화 |
|---|------|------|--------|
| 1 | 설비 기능 존재 여부 | DB 조회 (Text-to-SQL) | 자동 |
| 2 | 설비 간 기능 비교 | DB 조회 (Text-to-SQL) | 자동 |
| 3 | 설비 오동작 원인 | Splunk 로그 + 패턴 분석 | 반자동 (검토자 승인) |
| 4 | 기능 설명 | RAG (Confluence) | 자동 |

## 개발 우선순위

**Phase 1: DB Agent (Text-to-SQL) → Phase 2: RAG → Phase 3: Splunk → Phase 4: Knowledge Agent + 품질 자동화**

원칙: **Walking Skeleton + Vertical Slice**. 한 번에 다 만들지 않는다. Agent 인터페이스/LLM Provider/프롬프트 분리/SSE 계약/트레이스 ID 등 **경계만 Phase 1에 미리 잡고**, 기능은 Phase별로 끝까지 동작하게 추가.

## LLM 시스템 핵심 원칙 3가지 (매우 중요)

LLM 시스템은 일반 코드와 다르다. 아래 원칙은 코드 작성/리뷰/테스트 모든 단계에 적용.

### 1. 정확한 출력 일치를 검증하지 말 것
LLM은 비결정적. 같은 입력에 다른 출력이 정상.
- ❌ `assert response == "expected exact text"`
- ✅ 속성/계약 검증: JSON 스키마 통과, 필수 필드 존재, SQL 화이트리스트 통과, 인용 포함 여부, 신뢰도 범위
- ✅ 의미 유사도, 키워드 포함, 실행 결과 동치성

### 2. 프롬프트 변경은 항상 Golden Eval로 검증
프롬프트 한 줄 수정이 전체 품질을 망가뜨릴 수 있음.
- 프롬프트 파일(`config/prompts/*.j2`) 변경 PR은 **Golden Eval CI 필수 통과**
- 회귀 임계값(이전 main 점수 -5%) 초과 시 머지 차단
- 프롬프트는 코드와 동급의 자산. 리뷰/버전 관리/감사 대상.

### 3. Golden Dataset은 코드와 동급의 자산
- Day 1부터 Git에 커밋, PR 리뷰
- 운영 중 👎 받은 질문, 검토자 수정 답변은 분석 후 Golden에 추가
- 시간이 갈수록 데이터셋이 성장 → 품질 회귀 감지력 ↑
- Phase 1 종료 시점에 **baseline 점수 고정**, 이후 모든 변경은 baseline 대비 평가

## 운영 정책

- **외부 인프라:** Oracle 1개로 단순화. Redis/Kafka/별도 캐시 서버 사용 금지.
- **설정 3계층:**
  - **Code:** Agent 클래스, 핵심 로직 (PR 리뷰 + 배포)
  - **YAML:** 프롬프트 템플릿, 스키마 설명, 시드 패턴, 화이트리스트(보안 임계 → 절대 DB 금지)
  - **DB:** Few-shot 누적, Knowledge 항목, 임계값 오버라이드, Feature Flag (운영 핫리로드 가능)
- **핫리로드:** Pub/Sub 없이 `config_version` 테이블 30초 폴링
- **모든 LLM 호출에 trace_id** — 디버깅/품질 측정 기반

## 보안 원칙

- DB는 **read-only 커넥션** 전용
- SQL은 **SELECT만 허용**, 화이트리스트 테이블/컬럼만 접근 가능 (`config/whitelist.yaml` — DB 변경 금지)
- 모든 SQL은 LIMIT 자동 주입 (대용량 테이블은 WHERE 강제)
- Prompt injection 방어: 사용자 입력은 시스템 프롬프트와 명확히 분리
- LLM/DB 자격증명은 환경변수/Secret Manager. 코드/설정 파일에 절대 X

## 품질 게이트

| 단계 | 실행 | 기준 |
|------|------|------|
| Pre-commit | unit + integration | 100% pass |
| PR | + component (실 LLM) + Golden Eval | Golden 회귀 -5% 미만 |
| Nightly | Golden Full | 트렌드 리포트 자동 발행 |
| Pre-release | + 수동 E2E smoke | 핵심 시나리오 5~10개 통과 |

## 디렉토리 구조

```
app/
├── api/         # HTTP 계층 (라우터, 미들웨어)
├── core/        # 도메인 (FastAPI 무관, 단위 테스트 용이)
│   ├── orchestrator/
│   ├── agents/  # Agent ABC + 구현체 (플러그인 패턴)
│   └── synthesizer.py
├── infra/       # 외부 의존성 어댑터
│   ├── llm/     # Provider 인터페이스 (사내 LLM API)
│   ├── db/      # Oracle (read-only)
│   ├── rag/     # 사내 RAG API + Reranker
│   └── splunk/
├── eval/        # Golden Dataset 평가 러너
└── shared/      # Pydantic 모델, 예외, 로깅

config/          # YAML 설정 (프롬프트, 스키마, 패턴, 화이트리스트)
tests/
├── unit/
├── integration/
├── component/
└── golden/      # Golden Dataset + 평가 러너
docs/
└── superpowers/
    └── specs/   # 설계 문서
```

## 참고 설계 문서

- `docs/superpowers/specs/2026-04-18-tc-voc-chatbot-design.md` (작성 예정)

## 자주 하는 작업

- 새 데이터 소스 추가: `core/agents/`에 Agent 구현 + `AGENT_REGISTRY` 등록 + `config/agents.yaml` 추가 + Golden 시나리오 추가
- 프롬프트 튜닝: `config/prompts/*.j2` 수정 → Golden Eval 통과 확인 → PR
- 패턴/Few-shot 추가: 운영자 UI 또는 직접 DB INSERT (배포 불필요)
- 신규 화이트리스트 테이블 허용: `config/whitelist.yaml` 수정 + PR (보안 리뷰 필수)
