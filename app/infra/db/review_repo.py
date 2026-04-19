from __future__ import annotations
from app.infra.db.base import DBPool


class ReviewRepository:
    def __init__(self, app_pool: DBPool) -> None:
        self._pool = app_pool

    async def create_pending(
        self,
        session_id: str,
        trace_id: str,
        question: str,
        draft_answer: str,
        log_context: dict | None = None,
        confidence: float = 0.0,
    ) -> int:
        await self._pool.execute(
            """
            INSERT INTO pending_reviews
                (session_id, trace_id, question, draft_answer, log_context, confidence)
            VALUES
                (%(session_id)s, %(trace_id)s, %(question)s, %(draft_answer)s,
                 %(log_context)s, %(confidence)s)
            """,
            {
                "session_id": session_id,
                "trace_id": trace_id,
                "question": question,
                "draft_answer": draft_answer,
                "log_context": __import__("json").dumps(log_context) if log_context else None,
                "confidence": confidence,
            },
        )
        rows = await self._pool.fetch_all(
            "SELECT LAST_INSERT_ID() AS id"
        )
        return rows[0]["id"]

    async def get_pending(self, limit: int = 20) -> list[dict]:
        return await self._pool.fetch_all(
            """
            SELECT review_id, session_id, trace_id, question, draft_answer,
                   log_context, confidence, created_at
            FROM pending_reviews
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT %(limit)s
            """,
            {"limit": limit},
        )

    async def resolve(
        self,
        review_id: int,
        status: str,
        reviewer_id: str,
        final_answer: str | None = None,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE pending_reviews
            SET status = %(status)s,
                reviewer_id = %(reviewer_id)s,
                final_answer = %(final_answer)s,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE review_id = %(review_id)s
            """,
            {
                "status": status,
                "reviewer_id": reviewer_id,
                "final_answer": final_answer,
                "review_id": review_id,
            },
        )
