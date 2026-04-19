import pytest
from app.infra.splunk.pattern_analyzer import PatternAnalyzer


@pytest.fixture
def analyzer():
    return PatternAnalyzer()


def test_extract_error_codes(analyzer):
    events = [
        {"_raw": "2024-01-01 ERROR CODE=E1234 temperature out of range"},
        {"_raw": "2024-01-01 ERROR CODE=E5678 sensor failure"},
        {"_raw": "2024-01-01 INFO normal operation"},
    ]
    result = analyzer.analyze(events)
    assert "E1234" in result["error_codes"]
    assert "E5678" in result["error_codes"]
    assert result["error_count"] == 2


def test_empty_events(analyzer):
    result = analyzer.analyze([])
    assert result["error_count"] == 0
    assert result["error_codes"] == []
    assert result["summary"] == ""


def test_top_errors_summary(analyzer):
    events = [
        {"_raw": "ERROR CODE=E001 repeated failure"},
        {"_raw": "ERROR CODE=E001 repeated failure"},
        {"_raw": "ERROR CODE=E002 single failure"},
    ]
    result = analyzer.analyze(events)
    assert result["top_error"] == "E001"
    assert result["error_count"] == 3
    assert "E001" in result["summary"]
