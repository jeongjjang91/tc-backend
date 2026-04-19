import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "real_llm: mark test as requiring real LLM API")


@pytest.fixture
def sample_whitelist():
    return {
        "tables": {
            "PARAMETER": {"columns": ["param_id", "param_name", "eqp_id"], "requires_where_clause": True},
            "MODEL_INFO": {"columns": ["eqp_id", "model_name", "version"], "requires_where_clause": False},
            "DCOL_ITEM": {"columns": ["item_id", "item_name", "eqp_id", "dev_status"], "requires_where_clause": True},
        },
        "large_tables": ["DCOL_LOG"],
        "forbidden_functions": ["DBMS_", "UTL_"],
    }
