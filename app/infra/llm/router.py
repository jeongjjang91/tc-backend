from __future__ import annotations

from app.infra.llm.base import LLMProvider
from app.shared.exceptions import ConfigError


class ModelRouter:
    """Task-name to provider mapping for T20 multi-model routing."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        routing: dict[str, str],
        default_provider: str = "accurate",
    ) -> None:
        self._providers = providers
        self._routing = routing
        self._default_provider = default_provider

    def get(self, task: str) -> LLMProvider:
        provider_name = self._routing.get(task, self._default_provider)
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ConfigError(f"LLM provider not configured for task '{task}': {provider_name}")
        return provider
