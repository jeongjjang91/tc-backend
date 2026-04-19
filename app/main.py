from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.middleware.tracing import TracingMiddleware
from app.api.v1 import chat, feedback, review
from app.api.deps import init_dependencies
from app.shared.logging import setup_logging
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    await init_dependencies()
    yield


app = FastAPI(title="TC VOC Chatbot", lifespan=lifespan)
app.add_middleware(TracingMiddleware)
app.include_router(chat.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(review.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
