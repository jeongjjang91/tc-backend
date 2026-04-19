import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.shared.schemas import ChatRequest, Context
from app.api.deps import get_planner, get_executor, get_session_repo
from app.core.orchestrator.planner import QueryPlanner
from app.core.orchestrator.executor import QueryExecutor
from app.core.synthesizer import Synthesizer
from app.infra.db.sessions import SessionRepository
from app.shared.logging import get_trace_id, get_logger

router = APIRouter()
logger = get_logger(__name__)

_synthesizer = Synthesizer()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream(
    req: ChatRequest,
    planner: QueryPlanner,
    executor: QueryExecutor,
    session_repo: SessionRepository,
):
    trace_id = get_trace_id()
    ctx = Context(session_id=req.session_id, trace_id=trace_id)

    await session_repo.get_or_create(req.session_id, req.user_id)
    history = await session_repo.get_history(req.session_id, limit=6)
    await session_repo.save_message(req.session_id, "user", req.message, [], 1.0, trace_id)

    # Plan
    sub_queries = await planner.plan_async(req.message, req.session_id, history=history)
    for sq in sub_queries:
        yield _sse("plan", {"agent": sq.agent, "status": f"{sq.agent} 처리 중..."})

    # Execute
    results = await executor.execute(sub_queries, ctx)

    failed = [r for r in results if not r.success]
    if failed and not any(r.success for r in results):
        yield _sse("error", {"message": failed[0].error or "오류가 발생했습니다"})
        yield _sse("done", {})
        return

    success_results = [r for r in results if r.success]

    # Synthesize
    merged = await _synthesizer.synthesize(req.message, success_results, trace_id=trace_id)
    answer = merged["answer"]
    confidence = merged["confidence"]
    citations = [e.model_dump() for e in merged["evidence"]]

    for chunk in answer.split(" "):
        yield _sse("token", {"text": chunk + " "})

    yield _sse("citation", {"citations": citations})
    yield _sse("confidence", {"score": confidence, "needs_review": confidence < 0.7})

    msg_id = await session_repo.save_message(
        req.session_id, "assistant", answer, citations, confidence, trace_id
    )
    yield _sse("done", {"message_id": msg_id})


@router.post("/chat")
async def chat(
    req: ChatRequest,
    planner: QueryPlanner = Depends(get_planner),
    executor: QueryExecutor = Depends(get_executor),
    session_repo: SessionRepository = Depends(get_session_repo),
) -> StreamingResponse:
    return StreamingResponse(
        _stream(req, planner, executor, session_repo),
        media_type="text/event-stream",
    )
