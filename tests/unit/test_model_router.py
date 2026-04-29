import pytest

from app.infra.llm.router import ModelRouter
from app.shared.exceptions import ConfigError


def test_router_returns_correct_provider():
    fast = object()
    accurate = object()
    router = ModelRouter(
        providers={"fast": fast, "accurate": accurate},
        routing={"schema_linking": "fast", "sql_generation": "accurate"},
    )
    assert router.get("schema_linking") is fast
    assert router.get("sql_generation") is accurate


def test_router_uses_default_provider_for_unknown_task():
    accurate = object()
    router = ModelRouter(providers={"accurate": accurate}, routing={}, default_provider="accurate")
    assert router.get("unknown") is accurate


def test_router_raises_on_unknown_provider():
    router = ModelRouter(providers={"accurate": object()}, routing={"x": "missing"})
    with pytest.raises(ConfigError):
        router.get("x")
