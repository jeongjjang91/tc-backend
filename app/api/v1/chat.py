import json
import uuid
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.shared.schemas import ChatRequest, SubQuery, Context
from app.api.deps import get_db_agent, get_session_repo
from app.core.agents.db.agent import DBAgent
from app.infra.db.sessions import SessionRepository
from app.shared.logging import get_trace_id, get_logger

router = APIRouter()
logger = get_logger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream(req: ChatRequest, agent: DBAgent, session_repo: SessionRepository):
    trace_id = get_trace_id()
    ctx = Context(session_id=req.session_id, trace_id=trace_id)

    await session_repo.get_or_create(req.session_id, req.user_id)
    await session_repo.save_message(req.session_id, "user", req.message, [], 1.0, trace_id)

    yield _sse("plan", {"agent": "db", "status": "DB 조회 중..."})

    sub_query = SubQuery(id=str(uuid.uuid4()), agent="db", query=req.message)
    result = await agent.run(sub_query, ctx)

    if not result.success:
        yield _sse("error", {"message": result.error or "오류가 발생했습니다"})
        yield _sse("done", {})
        return

    answer = result.raw_data.get("answer", "") if result.raw_data else ""
    citations = [e.model_dump() for e in result.evidence]

    for chunk in answer.split(" "):
        yield _sse("token", {"text": chunk + " "})

    yield _sse("citation", {"citations": citations})
    yield _sse("confidence", {"score": result.confidence, "needs_review": result.confidence < 0.7})

    msg_id = await session_repo.save_message(
        req.session_id, "assistant", answer, citations, result.confidence, trace_id
    )
    yield _sse("done", {"message_id": msg_id})


@router.post("/chat")
async def chat(
    req: ChatRequest,
    agent: DBAgent = Depends(get_db_agent),
    session_repo: SessionRepository = Depends(get_session_repo),
) -> StreamingResponse:
    return StreamingResponse(_stream(req, agent, session_repo), media_type="text/event-stream")
