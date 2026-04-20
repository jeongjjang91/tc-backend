from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from app.api.deps import get_table_service
from app.infra.db.table_service import TableService, TableViewerError

router = APIRouter(prefix="/tables", tags=["tables"])


def _filters_from_request(request: Request, reserved: set[str]) -> dict[str, str]:
    """쿼리 파라미터에서 예약어(page, page_size 등) 제외한 나머지를 필터로 사용."""
    return {k: v for k, v in request.query_params.items() if k not in reserved}


@router.get("")
async def list_tables(svc: TableService = Depends(get_table_service)):
    return svc.list_tables()


@router.get("/{table_name}")
async def get_table_rows(
    table_name: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    svc: TableService = Depends(get_table_service),
):
    filters = _filters_from_request(request, {"page", "page_size"})
    try:
        return await svc.get_rows(table_name, filters, page, page_size)
    except TableViewerError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{table_name}/download")
async def download_table(
    table_name: str,
    request: Request,
    format: str = Query(default="csv", pattern="^(csv|excel)$"),
    limit: int | None = Query(default=None, ge=1, le=100_000),
    svc: TableService = Depends(get_table_service),
):
    filters = _filters_from_request(request, {"format", "limit"})
    try:
        generator = await svc.stream_download(table_name, filters, format, limit)
    except TableViewerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if format == "csv":
        filename = f"{table_name}.csv"
        media_type = "text/csv; charset=utf-8-sig"
    else:
        filename = f"{table_name}.xlsx"
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return StreamingResponse(
        generator,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
