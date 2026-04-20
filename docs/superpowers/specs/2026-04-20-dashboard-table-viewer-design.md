# Dashboard Table Viewer 설계

## 개요

TC DB(read-only MySQL) 테이블을 웹 대시보드에서 조회·필터·다운로드하는 API.
최대 30만 row 테이블을 페이지네이션 + 서버 캐싱으로 효율적으로 처리한다.

---

## 대상 테이블 (초기)

| 테이블 | 예상 row 수 | 필터 필수 |
|--------|------------|----------|
| TC_EQUIPMENT | ~5만 | 아니오 |
| TC_EQP_PARAM | ~30만 | 예 |
| TC_EQP_RELINK | 30만+ | 예 |

테이블은 `config/whitelist.yaml`에 추가하는 것만으로 자동 반영된다.

---

## API 엔드포인트

```
GET  /api/v1/tables
GET  /api/v1/tables/{table_name}
GET  /api/v1/tables/{table_name}/download
```

### 테이블 목록
```
GET /api/v1/tables
Response: [{"name": "TC_EQUIPMENT", "filterable": [...], "requires_where": false}, ...]
```

### 페이지네이션 조회
```
GET /api/v1/tables/TC_EQP_PARAM?page=1&page_size=50&LINEID=L01&EQPID=EQP01

Response:
{
  "total": 3200,
  "page": 1,
  "page_size": 50,
  "pages": 64,
  "data": [{...}, ...]
}
```

- `page_size` 최대 200
- 필터 파라미터: 컬럼명 = 값 형식 (whitelist `filterable` 컬럼만 허용)
- `requires_where_clause: true` 테이블은 필터 없으면 400 반환

### 다운로드
```
GET /api/v1/tables/TC_EQUIPMENT/download?format=csv&LINEID=L01
GET /api/v1/tables/TC_EQUIPMENT/download?format=excel&LINEID=L01&limit=5000
```

- `format`: `csv` 또는 `excel`
- `limit`: 생략 시 필터 결과 전체 (최대 100,000건)
- StreamingResponse로 전송 (메모리에 전체 적재 없음)
- Excel: `openpyxl` write_only 모드, 모든 셀 텍스트 포맷(`@`) 강제

---

## 캐싱 전략

| 조건 | 동작 |
|------|------|
| 필터 결과 ≤ 10,000건 | 서버 메모리 캐싱, 페이지 전환 시 DB 쿼리 없음 |
| 필터 결과 > 10,000건 | Offset 페이지네이션 (캐싱 없음) |
| 동일 필터 조건 | 사용자 공유 캐시 (100명이 같은 필터 → 캐시 1개) |

- TTL: 5분
- 최대 캐시 엔트리: 100개 (LRU 방식 삭제)
- 캐시 키: `hash(table_name + sorted(filters))`
- 별도 인프라 없음 (Python dict)

---

## 보안

- 테이블명: URL 파라미터 → whitelist 존재 여부 검증 (없으면 404)
- 필터 컬럼명: whitelist `filterable` 목록에서만 허용 (목록 외 무시)
- 필터 값: 파라미터 바인딩 (`%(name)s`)
- TC DB는 read-only 커넥션 (기존 정책 그대로)

---

## whitelist.yaml 확장 필드

```yaml
tables:
  TC_EQUIPMENT:
    columns: [LINEID, EQPID, SERVER_MODEL, DCOP_MODEL, JAR_NAME]
    filterable: [LINEID, EQPID, SERVER_MODEL, DCOP_MODEL]
    requires_where_clause: false
  TC_EQP_PARAM:
    columns: [LINEID, EQPID, SERVER_MODEL, PARAM_NAME, PARAM_VALUE]
    filterable: [LINEID, EQPID, SERVER_MODEL, PARAM_NAME]
    requires_where_clause: true
  TC_EQP_RELINK:
    columns: [EQP_ID, CEID, RPTID, RPT_ORDER, VID_LIST]
    filterable: [EQP_ID, CEID, RPTID]
    requires_where_clause: true
```

---

## 컴포넌트 구조

```
whitelist.yaml
      ↓
TableService (app/infra/db/table_service.py)
  - list_tables()
  - get_rows(table, filters, page, page_size)
  - stream_download(table, filters, format, limit)
      ↓
MySQLPool tc_pool (기존, read-only)
      ↓
/api/v1/tables 라우터 (app/api/v1/tables.py)
```

---

## DB 인덱스 권고 (DBA 요청)

필터 성능을 위해 아래 컬럼에 인덱스 필요:

| 테이블 | 인덱스 컬럼 |
|--------|------------|
| TC_EQUIPMENT | LINEID, EQPID |
| TC_EQP_PARAM | LINEID, EQPID, PARAM_NAME |
| TC_EQP_RELINK | EQP_ID, CEID |

---

## 프론트엔드 연동 (Vue.js)

```javascript
// 1. 테이블 목록 로드
const tables = await fetch('/api/v1/tables').then(r => r.json())

// 2. 조회 (필터 + 페이지)
const params = new URLSearchParams({ page: 1, page_size: 50, LINEID: 'L01' })
const result = await fetch(`/api/v1/tables/TC_EQP_PARAM?${params}`).then(r => r.json())

// 3. 다운로드
const params = new URLSearchParams({ format: 'excel', LINEID: 'L01' })
window.location.href = `/api/v1/tables/TC_EQP_PARAM/download?${params}`
```

---

## 향후 확장

- 대용량 다운로드(10만 건 이상) 백그라운드 export 잡
- 정렬(`sort_by`, `sort_order`) 파라미터 추가
- 부분 문자열 검색 (`PARAM_NAME__contains=TEMP`)
