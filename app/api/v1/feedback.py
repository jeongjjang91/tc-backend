from fastapi import APIRouter, Depends
from app.shared.schemas import FeedbackRequest
from app.infra.db.sessions import SessionRepository
from app.api.deps import get_session_repo

router = APIRouter()


@router.post("/feedback")
async def feedback(
    req: FeedbackRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
):
    await session_repo.pool.execute(
        "INSERT INTO feedback_log (message_id, rating, comment) VALUES (:mid, :rating, :comment)",
        {"mid": req.message_id, "rating": req.rating, "comment": req.comment},
    )
    return {"status": "ok"}
