from app.infra.db.few_shot_store import FewShotStore, extract_skeleton


def test_extract_skeleton_eqp_param():
    s = extract_skeleton("A 설비에 PARAM_X 있나?", known_eqps=["EQP_A_001"], known_params=["PARAM_X"])
    assert "<EQP>" in s
    assert "<PARAM>" in s


def test_extract_skeleton_no_match():
    s = extract_skeleton("기능이 뭐야?", known_eqps=[], known_params=[])
    assert s == "기능이 뭐야?"


def test_store_search_by_skeleton():
    store = FewShotStore()
    store.add_seed([
        {"question": "A 설비에 PARAM_X 있나?", "sql": "SELECT 1 FROM PARAMETER WHERE eqp_id='EQP_A_001' AND param_name='PARAM_X'"},
        {"question": "B 설비에 PARAM_Y 있나?", "sql": "SELECT 1 FROM PARAMETER WHERE eqp_id='EQP_B_001' AND param_name='PARAM_Y'"},
    ])
    results = store.search("C 설비에 PARAM_Z 있어?", top_k=2)
    assert len(results) > 0
    assert "sql" in results[0]
