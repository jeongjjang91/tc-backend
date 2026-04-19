from __future__ import annotations
import json
from app.infra.db.base import DBPool
from app.shared.logging import get_logger

logger = get_logger(__name__)


class SessionRepository:
    def __init__(self, app_pool: DBPool):
        self.pool = app_pool

    async def get_or_create(self, session_id: str, user_id: str) -> dict:
        rows = await self.pool.fetch_all(
            "SELECT session_id FROM chat_sessions WHERE session_id = %(sid)s",
            {"sid": session_id},
        )
        if not rows:
            await self.pool.execute(
                "INSERT INTO chat_sessions (session_id, user_id) VALUES (%(sid)s, %(uid)s)",
                {"sid": session_id, "uid": user_id},
            )
        return {"session_id": session_id, "user_id": user_id}

    async def save_message(self, session_id: str, role: str, content: str,
                           citations: list, confidence: float, trace_id: str) -> int:
        await self.pool.execute(
            "INSERT INTO chat_messages (session_id, role, content, citations, confidence, trace_id)"
            " VALUES (%(sid)s, %(role)s, %(content)s, %(citations)s, %(conf)s, %(tid)s)",
            {
                "sid": session_id, "role": role, "content": content,
                "citations": json.dumps(citations, ensure_ascii=False),
                "conf": confidence, "tid": trace_id,
            },
        )
        rows = await self.pool.fetch_all(
            "SELECT MAX(message_id) AS mid FROM chat_messages"
            " WHERE session_id = %(sid)s AND trace_id = %(tid)s",
            {"sid": session_id, "tid": trace_id},
        )
        return rows[0].get("mid", 0) if rows else 0

    async def get_history(self, session_id: str, limit: int = 10) -> list[dict]:
        return await self.pool.fetch_all(
            "SELECT role, content FROM chat_messages"
            " WHERE session_id = %(sid)s"
            " ORDER BY created_at DESC LIMIT %(lim)s",
            {"sid": session_id, "lim": limit},
        )
