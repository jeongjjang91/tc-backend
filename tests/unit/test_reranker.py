from app.infra.rag.reranker import TFIDFReranker


def test_rerank_returns_top_k():
    reranker = TFIDFReranker()
    chunks = [
        {"id": "1", "title": "PARAM_X", "content": "PARAM_X는 온도 관련 파라미터입니다"},
        {"id": "2", "title": "기타", "content": "관련없는 내용"},
        {"id": "3", "title": "PARAM_X 상세", "content": "PARAM_X의 상세 설명"},
    ]
    results = reranker.rerank("PARAM_X 기능 설명", chunks, top_k=2)
    assert len(results) == 2
    assert results[0]["id"] in ["1", "3"]


def test_rerank_empty_returns_empty():
    reranker = TFIDFReranker()
    assert reranker.rerank("query", [], top_k=3) == []


def test_rerank_top_k_larger_than_chunks():
    reranker = TFIDFReranker()
    chunks = [{"id": "1", "title": "A", "content": "내용"}]
    results = reranker.rerank("query", chunks, top_k=5)
    assert len(results) == 1
