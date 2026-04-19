import pytest
from app.core.agents.db.validator import SQLValidator
from app.shared.exceptions import SQLValidationError

WHITELIST = {
    "tables": {
        "PARAMETER": {
            "columns": ["param_id", "param_name", "eqp_id"],
            "requires_where_clause": True,
        },
        "MODEL_INFO": {
            "columns": ["eqp_id", "model_name", "version"],
            "requires_where_clause": False,
        },
    },
    "large_tables": ["DCOL_LOG"],
    "forbidden_functions": ["DBMS_", "UTL_"],
}


def make_validator():
    return SQLValidator(whitelist=WHITELIST)


def test_valid_select_passes():
    v = make_validator()
    sql = v.validate_and_fix(
        "SELECT param_name FROM PARAMETER WHERE eqp_id = 'EQP_A_001'"
    )
    assert "PARAMETER" in sql


def test_delete_blocked():
    v = make_validator()
    with pytest.raises(SQLValidationError) as exc:
        v.validate_and_fix("DELETE FROM PARAMETER WHERE 1=1")
    assert "SELECT" in exc.value.reason


def test_unknown_table_blocked():
    v = make_validator()
    with pytest.raises(SQLValidationError):
        v.validate_and_fix("SELECT * FROM SECRET_TABLE")


def test_forbidden_function_blocked():
    v = make_validator()
    with pytest.raises(SQLValidationError):
        v.validate_and_fix("SELECT DBMS_OUTPUT.PUT_LINE('x') FROM DUAL")


def test_rownum_injected_when_missing():
    v = make_validator()
    sql = v.validate_and_fix(
        "SELECT param_name FROM PARAMETER WHERE eqp_id = 'EQP_A_001'"
    )
    assert "ROWNUM" in sql.upper() or "FETCH" in sql.upper()


def test_where_required_for_large_table():
    v = make_validator()
    with pytest.raises(SQLValidationError):
        v.validate_and_fix("SELECT * FROM DCOL_LOG")


def test_column_not_in_whitelist_blocked():
    v = make_validator()
    with pytest.raises(SQLValidationError):
        v.validate_and_fix(
            "SELECT secret_col FROM PARAMETER WHERE eqp_id = 'X'"
        )
