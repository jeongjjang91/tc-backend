from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.api.deps import get_review_repo
from app.infra.db.review_repo import ReviewRepository

router = APIRouter(prefix="/review", tags=["review"])


class ResolveRequest(BaseModel):
    status: str  # approved / rejected / edited
    reviewer_id: str
    final_answer: Optional[str] = None


@router.get("/pending")
async def list_pending(
    limit: int = 20,
    repo: ReviewRepository = Depends(get_review_repo),
):
    return await repo.get_pending(limit=limit)


@router.post("/{review_id}/resolve")
async def resolve_review(
    review_id: int,
    body: ResolveRequest,
    repo: ReviewRepository = Depends(get_review_repo),
):
    if body.status not in ("approved", "rejected", "edited"):
        raise HTTPException(status_code=400, detail="Invalid status")
    if body.status == "edited" and not body.final_answer:
        raise HTTPException(status_code=400, detail="final_answer required for edited status")
    await repo.resolve(
        review_id=review_id,
        status=body.status,
        reviewer_id=body.reviewer_id,
        final_answer=body.final_answer,
    )
    return {"review_id": review_id, "status": body.status}
