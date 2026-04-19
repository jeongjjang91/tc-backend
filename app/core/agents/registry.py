from app.core.agents.base import Agent

AGENT_REGISTRY: dict[str, type[Agent]] = {}


def register(cls: type[Agent]) -> type[Agent]:
    AGENT_REGISTRY[cls.name] = cls
    return cls
