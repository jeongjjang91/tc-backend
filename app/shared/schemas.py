from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class SubQuery(BaseModel):
    id: str
    agent: str
    query: str
    depends_on: list[str] = []


class Evidence(BaseModel):
    id: str
    source_type: Literal["db_row", "log_line", "doc_chunk", "knowledge_entry"]
    content: str
    metadata: dict[str, Any] = {}


class AgentResult(BaseModel):
    sub_query_id: str
    success: bool
    evidence: list[Evidence]
    raw_data: Any
    confidence: float = Field(ge=0.0, le=1.0)
    error: Optional[str] = None


class Context(BaseModel):
    session_id: str
    trace_id: str
    history: list[dict[str, str]] = []


class ChatRequest(BaseModel):
    session_id: str
    message: str
    user_id: str = "anonymous"


class FeedbackRequest(BaseModel):
    message_id: int
    rating: Literal["P", "N"]
    comment: Optional[str] = None
