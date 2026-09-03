# tests/test_retrieval.py — grep-mode local paper retrieval (V4 Tier A)
import os
import shutil
import tempfile

from research_agent.retrieval import grep_papers, _tokenize


def _mk_papers_dir() -> str:
    d = tempfile.mkdtemp()
    return d


def _write_paper(papers_dir: str, paper_id: str, content: str):
    path = os.path.join(papers_dir, f"{paper_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_grep_papers_english_keyword():
    papers_dir = _mk_papers_dir()
    try:
        _write_paper(papers_dir, "paper_attention", "# Attention Is All You Need\n\nAttention mechanisms are a key innovation in neural networks. Transformers use self-attention.\n")
        _write_paper(papers_dir, "paper_banana", "# Banana Smoothie\n\nRecipes are delicious and healthy.\n")
        results = grep_papers("how does attention work in transformers", papers_dir, n_results=3)
        assert len(results) >= 1
        top = results[0]
        assert top["paper_id"] == "paper_attention"
        assert "attention" in top["text"].lower()
        assert top["score"] > 0
    finally:
        shutil.rmtree(papers_dir, ignore_errors=True)


def test_grep_papers_chinese_query():
    papers_dir = _mk_papers_dir()
    try:
        _write_paper(papers_dir, "cn_attention", "# Transformer 注意力机制\n\nTransformer 模型使用自注意力机制处理序列。注意力权重决定相关性。\n")
        _write_paper(papers_dir, "cn_hplc", "# HPLC 分析\n\n色谱柱分离化合物。\n")
        results = grep_papers("注意力机制 Transformer", papers_dir, n_results=3)
        assert results, "expected a Chinese query match"
        assert results[0]["paper_id"] == "cn_attention"
    finally:
        shutil.rmtree(papers_dir, ignore_errors=True)


def test_grep_papers_ranks_more_relevant_higher():
    papers_dir = _mk_papers_dir()
    try:
        _write_paper(papers_dir, "p_full", "# Attention\n\nattention attention attention. transformer. neural. sequence. model. encode. decode.\n")
        _write_paper(papers_dir, "p_partial", "# Other\n\nattention appears once but mostly about banana recipes.\n")
        results = grep_papers("attention transformer neural", papers_dir, n_results=2)
        assert results[0]["paper_id"] == "p_full"
    finally:
        shutil.rmtree(papers_dir, ignore_errors=True)


def test_grep_papers_empty_dir():
    papers_dir = tempfile.mkdtemp()
    try:
        results = grep_papers("anything", papers_dir, n_results=3)
        assert results == []
    finally:
        shutil.rmtree(papers_dir, ignore_errors=True)


def test_grep_papers_missing_dir():
    assert grep_papers("query", "C:/definitely/not/a/dir", n_results=3) == []


def test_grep_papers_snippet_has_context():
    papers_dir = _mk_papers_dir()
    try:
        body = "\n".join(f"unrelated line {i}" for i in range(20))
        _write_paper(papers_dir, "ctx", f"# Paper\n\n{body}\n\nHere is the magic attention sentence in the middle.\n\n{body}\n")
        results = grep_papers("magic attention sentence", papers_dir, n_results=1)
        assert results
        assert "magic attention" in results[0]["text"]
    finally:
        shutil.rmtree(papers_dir, ignore_errors=True)


def test_tokenize_mixed():
    toks = _tokenize("Transformer注意力机制 in NLP")
    assert "Transformer" in toks
    assert "注意力" in toks
    assert "nlp" in toks
