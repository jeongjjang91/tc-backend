from app.infra.db.value_store import ValueStore


def test_find_candidates_for_eqp():
    store = ValueStore()
    store.load_values("eqp_id", ["EQP_A_001", "EQP_A_002", "EQP_B_001"])
    hits = store.find_candidates("A 설비", top_n=2)
    assert any("EQP_A" in h for h in hits)


def test_find_candidates_exact_match():
    store = ValueStore()
    store.load_values("param_name", ["PARAM_TEMP", "PARAM_PRESSURE", "PARAM_FLOW"])
    hits = store.find_candidates("PARAM_TEMP", top_n=3)
    assert "PARAM_TEMP" in hits


def test_extract_candidates_from_question():
    store = ValueStore()
    store.load_values("eqp_id", ["EQP_A_001", "EQP_B_001"])
    store.load_values("param_name", ["PARAM_X", "PARAM_Y"])
    result = store.extract_from_question("A 설비에 PARAM_X 있어?")
    assert len(result) > 0
