from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.shared.logging import new_trace_id
import structlog


class TracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        tid = request.headers.get("X-Trace-Id") or new_trace_id()
        structlog.contextvars.bind_contextvars(trace_id=tid)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        structlog.contextvars.clear_contextvars()
        return response
