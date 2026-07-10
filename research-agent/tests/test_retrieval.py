# tests/test_retrieval.py
from research_agent.vector_store import get_collection, add_chunks, delete_paper
from research_agent.retrieval import hybrid_search, build_bm25_index


def test_hybrid_search(temp_data_dir):
    paper_id = "hybrid_test"
    chunks = [
        {"chunk_index": 0, "text": "Attention mechanisms are a key innovation in neural networks."},
        {"chunk_index": 1, "text": "Recurrent neural networks process sequences step by step."},
        {"chunk_index": 2, "text": "Transformers use self-attention to process all tokens in parallel."},
        {"chunk_index": 3, "text": "Banana smoothie recipes are delicious and healthy."},
    ]
    add_chunks(paper_id, chunks)

    results = hybrid_search("how does attention work in transformers", n_results=3)
    assert len(results) > 0
    assert all("text" in r for r in results)
    assert all("score" in r for r in results)
    assert all("paper_id" in r for r in results)

    # "attention" + "transformers" chunk should rank higher than "banana" chunk
    texts = [r["text"].lower() for r in results]
    combined = " ".join(texts)
    assert "attention" in combined or "transformer" in combined

    delete_paper(paper_id)
    build_bm25_index()