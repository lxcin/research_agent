"""Tier B memory retrieval evaluation — Recall@k / Precision@k / MRR / Hit@1.

Standalone, deterministic (keyword path needs no model/network). If the optional
vector layer is enabled (RESEARCH_AGENT_MEMORY_VECTOR=1 + sentence-transformers
+ local model), it also reports the hybrid (RRF) numbers for comparison.

Run:
    PYTHONPATH=src python tests/eval_memory_recall.py

Design (avoid self-fulfilling labels):
  - Query categories:
      lexical    : high surface overlap with the gold unit (keyword path should win)
      paraphrase : synonymic rewrite, little/no surface overlap (only vectors can help)
      aggregate  : "what are my preferences"-style multi-gold queries
  - Every query is labeled with the set of gold unit ids BEFORE retrieval.
"""
import os
import sys
import tempfile

# Isolate data dir before importing storage (it caches the DB connection).
_EVAL_DIR = tempfile.mkdtemp(prefix="mem_eval_")
os.environ["RESEARCH_AGENT_DATA_DIR"] = _EVAL_DIR

from research_agent.memory import storage, vector  # noqa: E402
from research_agent.memory.tier_b import MemoryManager  # noqa: E402
from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope  # noqa: E402


# ── Labeled corpus ──────────────────────────────────────────────────────────
# (id, text, kind, importance)
UNITS = [
    ("u01", "用户偏好用中文写邮件并用中文签名", MemoryKind.PREFERENCE, 0.8),
    ("u02", "用户研究注意力机制与Transformer可解释性", MemoryKind.FACT, 0.9),
    ("u03", "用户导师姓王，在北京理工大学", MemoryKind.FACT, 0.7),
    ("u04", "用户做过HPLC实验但失败了，原因是色谱柱污染", MemoryKind.DEAD_END, 0.6),
    ("u05", "用户偏好用PyTorch而不是TensorFlow", MemoryKind.PREFERENCE, 0.8),
    ("u06", "用户是自然语言处理方向的研究生", MemoryKind.FACT, 0.9),
    ("u07", "用户在做一个论文综述项目，计划本月底完成", MemoryKind.TASK, 0.7),
    ("u08", "用户喜欢简短直接的回答，不要长篇解释", MemoryKind.STYLE, 0.8),
    ("u09", "用户之前用LangGraph做agent失败过，原因是调试困难", MemoryKind.DEAD_END, 0.7),
    ("u10", "用户偏好本地优先的分发方式，担心云端隐私", MemoryKind.PREFERENCE, 0.6),
    ("u11", "用户常用DeepSeek作为默认模型", MemoryKind.PREFERENCE, 0.7),
    ("u12", "用户所在实验室每周三开组会", MemoryKind.FACT, 0.5),
    ("u13", "用户对中文分词很敏感，强调必须用jieba", MemoryKind.PREFERENCE, 0.7),
    ("u14", "用户之前尝试过向量检索中文效果差，P@1只有62%", MemoryKind.DEAD_END, 0.6),
    ("u15", "用户希望代码改动有版本控制和回滚能力", MemoryKind.PREFERENCE, 0.7),
    ("u16", "用户的英文名叫Alex", MemoryKind.FACT, 0.5),
    ("u17", "用户不喜欢在回复里出现道歉和客套话", MemoryKind.STYLE, 0.8),
    ("u18", "用户的研究方向是RAG与检索增强生成", MemoryKind.FACT, 0.8),
    ("u19", "用户偏好Markdown格式的输出", MemoryKind.PREFERENCE, 0.6),
    ("u20", "用户在Windows上开发，使用Edge WebView2", MemoryKind.FACT, 0.5),
    ("u21", "用户因为onnx安装失败放弃了某个方案", MemoryKind.DEAD_END, 0.5),
    ("u22", "用户偏好把配置放在本地config.yml，不用数据库", MemoryKind.PREFERENCE, 0.6),
    ("u23", "用户目标是把项目做成通用个人助手", MemoryKind.DECISION, 0.9),
    ("u24", "用户认为能测试的确定性代码比prompt更重要", MemoryKind.INSIGHT, 0.8),
    ("u25", "用户用GitHub Actions做CI，镜像推到GHCR", MemoryKind.FACT, 0.5),
    ("u26", "用户偏好函数式、少状态的设计", MemoryKind.PREFERENCE, 0.6),
    ("u27", "用户之前踩坑：把密钥明文写进了config被扫描拦截", MemoryKind.DEAD_END, 0.7),
    ("u28", "用户的毕业设计选题和agent记忆系统相关", MemoryKind.FACT, 0.7),
    ("u29", "用户偏好给AI足够的反例而不是只说要求", MemoryKind.STYLE, 0.6),
    ("u30", "用户对论文引用格式要求用[N]编号", MemoryKind.STYLE, 0.5),
]

# (query, gold_ids, category)
QUERIES = [
    # lexical: surface overlap present
    ("用户偏好什么写邮件", {"u01"}, "lexical"),
    ("用户研究什么方向", {"u02", "u06", "u18"}, "lexical"),
    ("用户导师姓什么", {"u03"}, "lexical"),
    ("HPLC实验为什么失败", {"u04"}, "lexical"),
    ("用户用PyTorch还是TensorFlow", {"u05"}, "lexical"),
    ("组会什么时候开", {"u12"}, "lexical"),
    ("用户名是什么", {"u16"}, "lexical"),
    ("RAG研究方向", {"u18"}, "lexical"),
    ("默认用什么模型", {"u11"}, "lexical"),
    ("用什么做CI", {"u25"}, "lexical"),

    # paraphrase: little surface overlap with gold text
    ("用户怎么写落款", {"u01"}, "paraphrase"),
    ("用户对回复字数的要求", {"u08"}, "paraphrase"),
    ("用户排斥哪些客套表述", {"u17"}, "paraphrase"),
    ("用户深度学习框架选型", {"u05"}, "paraphrase"),
    ("用户在意数据放不放在云上", {"u10"}, "paraphrase"),
    ("用户做agent踩过什么坑", {"u09"}, "paraphrase"),
    ("用户对代码可撤销性的诉求", {"u15"}, "paraphrase"),
    ("用户分词工具的选择", {"u13"}, "paraphrase"),
    ("用户之前检索遇到的失败", {"u14"}, "paraphrase"),
    ("用户对密钥安全的教训", {"u27"}, "paraphrase"),

    # aggregate: multiple golds
    ("用户有哪些偏好", {"u01", "u05", "u10", "u11", "u13", "u19", "u22", "u26"}, "aggregate"),
    ("用户的写作风格要求", {"u08", "u17", "u19", "u29", "u30"}, "aggregate"),
    ("用户踩过的坑有哪些", {"u04", "u09", "u14", "u21", "u27"}, "aggregate"),
    ("用户的技术偏好", {"u05", "u10", "u13", "u15", "u22", "u26"}, "aggregate"),
]


def _seed(mgr, units):
    for uid, text, kind, imp in units:
        mgr.write(MemoryUnit(id=uid, text=text, kind=kind,
                             importance=imp, scope=MemoryScope.USER))


def _metrics_for(retriever, limit=5):
    """Return (overall, by_category). retriever(query, k) -> list[str] of ids."""
    cat = {}
    recall_k = {1: [], 3: [], 5: []}
    prec5, hit1, mrr = [], [], []
    for query, gold, category in QUERIES:
        ranked = retriever(query, 5)
        gold = set(gold)
        first_rank = next((i + 1 for i, uid in enumerate(ranked) if uid in gold), 0)
        for k in (1, 3, 5):
            recall_k[k].append(len(set(ranked[:k]) & gold) / len(gold))
        prec5.append(len(set(ranked[:5]) & gold) / 5)
        hit1.append(1.0 if first_rank == 1 else 0.0)
        mrr.append(1.0 / first_rank if first_rank else 0.0)
        c = cat.setdefault(category, {"r1": [], "r5": [], "p5": [], "hit1": [], "mrr": []})
        c["r1"].append(recall_k[1][-1]); c["r5"].append(recall_k[5][-1])
        c["p5"].append(prec5[-1]); c["hit1"].append(hit1[-1]); c["mrr"].append(mrr[-1])

    def _avg(xs):
        return sum(xs) / len(xs) if xs else 0.0

    overall = {
        "R@1": _avg(recall_k[1]), "R@3": _avg(recall_k[3]), "R@5": _avg(recall_k[5]),
        "P@5": _avg(prec5), "Hit@1": _avg(hit1), "MRR": _avg(mrr), "N": len(QUERIES),
    }
    by_cat = {c: {"R@1": _avg(v["r1"]), "R@5": _avg(v["r5"]),
                  "P@5": _avg(v["p5"]), "Hit@1": _avg(v["hit1"]), "MRR": _avg(v["mrr"]),
                  "N": len(v["r1"])} for c, v in cat.items()}
    return overall, by_cat


def _fmt(name, overall):
    print(f"\n[{name}]  N={overall['N']}")
    print(f"  Recall@1={overall['R@1']:.1%}  Recall@3={overall['R@3']:.1%}  "
          f"Recall@5={overall['R@5']:.1%}")
    print(f"  Precision@5={overall['P@5']:.1%}  Hit@1={overall['Hit@1']:.1%}  "
          f"MRR={overall['MRR']:.3f}")


def _load_local_embedder():
    """Try to load BAAI/bge-small-zh locally for a chroma-free vector baseline.

    Returns an object with .rank(query, docs) -> list[(doc_id, sim)] or None.
    (chromadb's rust binding may be unavailable; this bypasses it entirely.)
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except Exception:
        return None
    try:
        model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
    except Exception:
        try:
            model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
        except Exception:
            return None

    class _Emb:
        def encode(self, texts):
            return np.array(model.encode(list(texts), normalize_embeddings=True))

    return _Emb()


def _hybrid_retriever_factory(mgr, emb):
    """RRF fusion of keyword ranking + cosine vector ranking (no chroma).

    Mirrors tier_b.MemoryManager.retrieve's RRF (k=60) but uses a local
    embedding backend so the comparison is reproducible without chromadb.
    """
    import numpy as np
    ids = [u.id for u in storage.list_units(active_only=True, limit=500)]
    texts = [u.text for u in storage.list_units(active_only=True, limit=500)]
    doc_emb = emb.encode(texts) if texts else None
    RRF_K = 60

    def retriever(query, k):
        kw = [u.id for u in storage.search_keyword(query, limit=k * 3)]
        if doc_emb is None:
            return kw[:k]
        q = emb.encode([query])[0]
        sims = doc_emb @ q
        order = list(np.argsort(-sims))
        vec = [ids[i] for i in order[: k * 3]]
        scores = {}
        for rank, uid in enumerate(kw):
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, uid in enumerate(vec):
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (RRF_K + rank + 1)
        return [u for u, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)][:k]

    return retriever


def main():
    storage.reset_db_for_tests()
    mgr = MemoryManager()
    _seed(mgr, UNITS)
    print(f"Memory recall eval: {len(UNITS)} units, {len(QUERIES)} queries")
    print(f"Data dir: {_EVAL_DIR}")

    # keyword-only path
    def keyword_retriever(query, k):
        return [u.id for u in storage.search_keyword(query, limit=k)]

    overall_kw, cats_kw = _metrics_for(keyword_retriever)
    _fmt("keyword-only (default)", overall_kw)
    print("  by category:")
    for c, m in sorted(cats_kw.items()):
        print(f"    {c:<11} R@1={m['R@1']:.0%} R@5={m['R@5']:.0%} "
              f"Hit@1={m['Hit@1']:.0%} MRR={m['MRR']:.2f} (N={m['N']})")

    # hybrid path (keyword + local embedding RRF; chroma-free, reproducible)
    emb = _load_local_embedder()
    if emb is not None:
        print(f"Embedding backend: BAAI/bge-small-zh-v1.5 (local, cosine + RRF)")
        hybrid_retriever = _hybrid_retriever_factory(mgr, emb)
        overall_hy, cats_hy = _metrics_for(hybrid_retriever)
        _fmt("hybrid keyword+vector (RRF)", overall_hy)
        print("  by category:")
        for c, m in sorted(cats_hy.items()):
            print(f"    {c:<11} R@1={m['R@1']:.0%} R@5={m['R@5']:.0%} "
                  f"Hit@1={m['Hit@1']:.0%} MRR={m['MRR']:.2f} (N={m['N']})")
        print("\n  delta (hybrid - keyword):")
        for key in ("R@1", "R@3", "R@5", "Hit@1"):
            print(f"    {key:<6} {overall_hy[key] - overall_kw[key]:+.1%}  "
                  f"({overall_kw[key]:.1%} -> {overall_hy[key]:.1%})")
    else:
        print("\n(local embedding model unavailable — hybrid comparison skipped; "
              "install sentence-transformers + cache BAAI/bge-small-zh-v1.5)")

    return overall_kw, cats_kw


if __name__ == "__main__":
    main()
