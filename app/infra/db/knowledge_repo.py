from __future__ import annotations
from app.infra.db.base import DBPool


class KnowledgeRepository:
    def __init__(self, app_pool: DBPool) -> None:
        self._pool = app_pool

    async def search(self, query: str, category: str | None = None, limit: int = 5) -> list[dict]:
        base_sql = """
            SELECT item_id, category, title, content, keywords, source
            FROM knowledge_items
            WHERE is_active = 1
              AND (title LIKE %(like_q)s OR content LIKE %(like_q)s)
        """
        params: dict = {"like_q": f"%{query}%", "limit": limit}
        if category:
            base_sql += " AND category = %(category)s"
            params["category"] = category
        base_sql += " ORDER BY updated_at DESC LIMIT %(limit)s"
        return await self._pool.fetch_all(base_sql, params)

    async def get_by_id(self, item_id: int) -> dict | None:
        rows = await self._pool.fetch_all(
            "SELECT * FROM knowledge_items WHERE item_id = %(item_id)s AND is_active = 1",
            {"item_id": item_id},
        )
        return rows[0] if rows else None

    async def create(
        self,
        category: str,
        title: str,
        content: str,
        keywords: list[str] | None = None,
        source: str | None = None,
        created_by: str = "system",
    ) -> None:
        import json
        await self._pool.execute(
            """
            INSERT INTO knowledge_items (category, title, content, keywords, source, created_by)
            VALUES (%(category)s, %(title)s, %(content)s, %(keywords)s, %(source)s, %(created_by)s)
            """,
            {
                "category": category,
                "title": title,
                "content": content,
                "keywords": json.dumps(keywords, ensure_ascii=False) if keywords else None,
                "source": source,
                "created_by": created_by,
            },
        )
