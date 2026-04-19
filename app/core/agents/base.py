from abc import ABC, abstractmethod
from app.shared.schemas import SubQuery, AgentResult, Context


class Agent(ABC):
    name: str = ""

    @abstractmethod
    async def run(self, sub_query: SubQuery, context: Context) -> AgentResult: ...
