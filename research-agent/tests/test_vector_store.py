# tests/test_vector_store.py
from research_agent.vector_store import get_collection, add_chunks, search, delete_paper


def test_add_and_search(temp_data_dir):
    paper_id = "test_paper_1"
    chunks = [
        {"chunk_index": 0, "text": "The Transformer architecture revolutionized NLP."},
        {"chunk_index": 1, "text": "Attention mechanisms compute weighted sums of values."},
        {"chunk_index": 2, "text": "Convolutional neural networks are used in image processing."},
    ]
    add_chunks(paper_id, chunks)

    results = search("attention mechanism in transformers", n_results=2)
    assert len(results["ids"][0]) > 0
    ids = results["ids"][0]
    assert paper_id in ids[0]

    # Verify metadata
    metadatas = results["metadatas"][0]
    assert all("paper_id" in m for m in metadatas)


def test_delete_paper_chunks(temp_data_dir):
    paper_id = "test_paper_del"
    add_chunks(paper_id, [{"chunk_index": 0, "text": "Ephemeral content."}])

    pre_search = search("ephemeral", n_results=1)
    assert len(pre_search["ids"][0]) > 0

    delete_paper(paper_id)

    post_search = search("ephemeral", n_results=1)
    ids = post_search["ids"][0]
    # After deletion, either no results or the paper_id should not appear
    assert len(ids) == 0 or paper_id not in ids[0]