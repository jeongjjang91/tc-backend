import pytest
from app.infra.config.loader import ConfigLoader


def test_load_thresholds(tmp_path):
    yaml_file = tmp_path / "thresholds.yaml"
    yaml_file.write_text("confidence_auto_send: 0.75\nmax_refine_attempts: 2\n")
    loader = ConfigLoader(config_dir=str(tmp_path))
    cfg = loader.load_thresholds()
    assert cfg["confidence_auto_send"] == 0.75
    assert cfg["max_refine_attempts"] == 2


def test_load_whitelist(tmp_path):
    yaml_file = tmp_path / "whitelist.yaml"
    yaml_file.write_text(
        "tables:\n  PARAMETER:\n    columns: [param_id, param_name]\n    requires_where_clause: true\n"
        "large_tables: [DCOL_LOG]\nforbidden_functions: [DBMS_]\n"
    )
    loader = ConfigLoader(config_dir=str(tmp_path))
    wl = loader.load_whitelist()
    assert "PARAMETER" in wl["tables"]
    assert wl["tables"]["PARAMETER"]["requires_where_clause"] is True
