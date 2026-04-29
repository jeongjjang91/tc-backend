from __future__ import annotations

from app.core.agents.base import Agent
from app.core.agents.registry import register
from app.shared.schemas import AgentResult, Context, SubQuery


@register
class SmallTalkAgent(Agent):
    name = "smalltalk"

    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult:
        message = sub_query.query.strip()
        if not message:
            answer = "질문 내용을 입력해 주세요."
        elif any(token in message.lower() for token in ("고마워", "감사", "thanks", "thank you")):
            answer = "도움이 됐다니 다행입니다."
        elif any(token in message.lower() for token in ("안녕", "hello", "hi")):
            answer = "안녕하세요. TC 설비, 파라미터, 로그, 문서 관련 질문을 도와드릴게요."
        else:
            answer = "TC 설비, 파라미터, 로그, 문서와 관련된 질문을 입력해 주세요."

        return AgentResult(
            sub_query_id=sub_query.id,
            success=True,
            evidence=[],
            raw_data={"answer": answer, "intent": "smalltalk"},
            confidence=1.0,
        )
