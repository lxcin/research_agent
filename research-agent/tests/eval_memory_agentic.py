"""Credible Tier B memory recall eval with hard negatives + agentic (LLM) expansion.

Three retrieval strategies on the same corpus:
  1) keyword-only        : simplest recall (jieba term-coverage ranking)
  2) hybrid (vector+RRF) : local bge-small-zh cosine + keyword, RRF fusion
  3) agentic (LLM expand): DeepSeek rewrites the query into N variants, each
                           retrieved via hybrid, then RRF-fused across variants

Corpus deliberately includes HARD NEGATIVES: units that are topically adjacent
(same theme, similar wording) but are NOT the gold answer, so metrics reflect
real discrimination rather than easy 30-item matching.

Run:
    PYTHONPATH=src python tests/eval_memory_agentic.py
    PYTHONPATH=src DEEPSEEK_API_KEY=... python tests/eval_memory_agentic.py   # enables agentic layer
"""
import os
import sys
import re
import tempfile

os.environ.setdefault("RESEARCH_AGENT_DATA_DIR", tempfile.mkdtemp(prefix="mem_agentic_"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from research_agent.memory import storage  # noqa: E402
from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope  # noqa: E402

RRF_K = 60


# ── Corpus with hard-negative clusters ──────────────────────────────────────
# (id, text, kind, importance). *decoy* = hard negative (adjacent, not gold).
UNITS = [
    # Cluster: identity
    ("u01", "用户是自然语言处理方向的研究生", MemoryKind.FACT, 0.9),
    ("u02", "用户的英文名叫Alex", MemoryKind.FACT, 0.5),
    ("u03", "用户的毕业设计选题和agent记忆系统相关", MemoryKind.FACT, 0.7),
    ("u04", "用户的导师姓王，在北京理工大学", MemoryKind.FACT, 0.7),
    ("u05", "用户的室友是计算机视觉方向", MemoryKind.FACT, 0.3),           # decoy(identity/NLP-adjacent)
    # Cluster: research direction
    ("u06", "用户研究注意力机制与Transformer可解释性", MemoryKind.FACT, 0.9),
    ("u07", "用户的研究方向是RAG与检索增强生成", MemoryKind.FACT, 0.8),
    ("u08", "用户最近在看关于图神经网络的工作", MemoryKind.FACT, 0.5),      # adjacent topic
    # Cluster: deep-learning framework
    ("u09", "用户偏好用PyTorch做深度学习实验", MemoryKind.PREFERENCE, 0.8),
    ("u10", "用户觉得TensorFlow的静态图调试麻烦", MemoryKind.PREFERENCE, 0.6),
    ("u11", "用户同事主要用JAX写研究代码", MemoryKind.FACT, 0.3),           # decoy(framework)
    # Cluster: writing style (many style prefs → aggregate + traps)
    ("u12", "用户偏好用中文写邮件并用中文签名", MemoryKind.PREFERENCE, 0.8),
    ("u13", "用户喜欢简短直接的回答，不要长篇解释", MemoryKind.STYLE, 0.8),
    ("u14", "用户不喜欢在回复里出现道歉和客套话", MemoryKind.STYLE, 0.8),
    ("u15", "用户偏好Markdown格式的输出", MemoryKind.PREFERENCE, 0.6),
    ("u16", "用户偏好给AI足够的反例而不是只说要求", MemoryKind.STYLE, 0.6),
    ("u17", "用户对论文引用格式要求用[N]编号", MemoryKind.STYLE, 0.5),
    ("u18", "用户读过一篇讲英文邮件礼仪的文章", MemoryKind.FACT, 0.2),       # decoy(邮件/style)
    # Cluster: tooling / config
    ("u19", "用户常用DeepSeek作为默认模型", MemoryKind.PREFERENCE, 0.7),
    ("u20", "用户偏好把配置放在本地config.yml，不用数据库", MemoryKind.PREFERENCE, 0.6),
    ("u21", "用户偏好本地优先的分发方式，担心云端隐私", MemoryKind.PREFERENCE, 0.6),
    ("u22", "用户用GitHub Actions做CI，镜像推到GHCR", MemoryKind.FACT, 0.5),
    ("u23", "用户在Windows上开发，使用Edge WebView2", MemoryKind.FACT, 0.5),
    ("u24", "用户强调中文分词必须用jieba", MemoryKind.PREFERENCE, 0.7),
    ("u25", "用户喜欢用VSCode而不是PyCharm", MemoryKind.PREFERENCE, 0.4),    # decoy(tooling)
    ("u26", "用户偏好把密钥放在环境变量而不是代码里", MemoryKind.PREFERENCE, 0.7),
    # Cluster: dead-ends / pitfalls
    ("u27", "用户做过HPLC实验但失败了，原因是色谱柱污染", MemoryKind.DEAD_END, 0.6),
    ("u28", "用户之前用LangGraph做agent失败过，原因是调试困难", MemoryKind.DEAD_END, 0.7),
    ("u29", "用户之前试过向量检索中文效果差，P@1只有62%", MemoryKind.DEAD_END, 0.6),
    ("u30", "用户因为onnx安装失败放弃了某个方案", MemoryKind.DEAD_END, 0.5),
    ("u31", "用户之前把密钥明文写进config被扫描拦截", MemoryKind.DEAD_END, 0.7),
    ("u32", "用户曾在一篇论文复现上卡了两周", MemoryKind.DEAD_END, 0.5),     # decoy(pitfall)
    # Cluster: tasks / decisions
    ("u33", "用户在做一个论文综述项目，计划本月底完成", MemoryKind.TASK, 0.7),
    ("u34", "用户目标是把项目做成通用个人助手", MemoryKind.DECISION, 0.9),
    ("u35", "用户决定删除论文检索相关模块，专注通用能力", MemoryKind.DECISION, 0.8),
    ("u36", "用户计划下个月开始找实习", MemoryKind.TASK, 0.6),
    ("u37", "用户上周提交了一篇会议论文", MemoryKind.FACT, 0.5),             # decoy(task/paper)
    # Cluster: design principles / insights
    ("u38", "用户认为能测试的确定性代码比prompt更重要", MemoryKind.INSIGHT, 0.8),
    ("u39", "用户偏好函数式、少状态的设计", MemoryKind.PREFERENCE, 0.6),
    ("u40", "用户希望代码改动有版本控制和回滚能力", MemoryKind.PREFERENCE, 0.7),
    ("u41", "用户强调架构解耦比功能堆叠更重要", MemoryKind.INSIGHT, 0.8),
    ("u42", "用户用过一段时间的响应式编程范式", MemoryKind.FACT, 0.3),       # decoy(design)
    # Cluster: misc facts
    ("u43", "用户所在实验室每周三开组会", MemoryKind.FACT, 0.5),
    ("u44", "用户每天通勤大概一小时", MemoryKind.FACT, 0.3),
    ("u45", "用户会弹吉他", MemoryKind.FACT, 0.3),
    ("u46", "用户在学日语，目标是N2", MemoryKind.FACT, 0.4),
    ("u47", "用户养了一只叫豆豆的猫", MemoryKind.FACT, 0.3),
    ("u48", "用户最近在读《设计数据密集型应用》", MemoryKind.FACT, 0.5),
]

# (query, gold_ids, category)
QUERIES = [
    # lexical: surface overlap
    ("用户导师是谁", {"u04"}, "lexical"),
    ("用户用什么模型", {"u19"}, "lexical"),
    ("用户用什么分词工具", {"u24"}, "lexical"),
    ("用户做什么CI", {"u22"}, "lexical"),
    ("用户养了什么宠物", {"u47"}, "lexical"),
    ("用户学什么外语", {"u46"}, "lexical"),
    ("用户研究RAG吗", {"u07"}, "lexical"),
    ("HPLC实验为什么失败", {"u27"}, "lexical"),

    # paraphrase: little surface overlap (needs semantics)
    ("用户怎么写落款", {"u12"}, "paraphrase"),
    ("用户对回复长度的要求", {"u13"}, "paraphrase"),
    ("用户排斥哪些客套表述", {"u14"}, "paraphrase"),
    ("用户的深度学习框架选型", {"u09"}, "paraphrase"),
    ("用户在意数据是否上云", {"u21"}, "paraphrase"),
    ("用户做agent踩过哪些坑", {"u28"}, "paraphrase"),
    ("用户对代码可撤销性的诉求", {"u40"}, "paraphrase"),
    ("用户对密钥管理的做法", {"u26"}, "paraphrase"),
    ("用户认为什么比提示词更重要", {"u38"}, "paraphrase"),

    # trap: hard-negative adjacent cluster present
    ("用户室友的研究方向", {"u05"}, "trap"),
    ("用户同事用什么框架", {"u11"}, "trap"),
    ("用户读过的关于邮件的文章", {"u18"}, "trap"),
    ("用户IDE的偏好", {"u25"}, "trap"),
    ("用户用什么编程范式", {"u39", "u42"}, "trap"),

    # aggregate: multiple gold
    ("用户有哪些偏好", {"u09", "u12", "u15", "u19", "u20", "u21", "u24", "u26", "u40"}, "aggregate"),
    ("用户的写作风格要求", {"u12", "u13", "u14", "u15", "u16", "u17"}, "aggregate"),
    ("用户踩过的坑有哪些", {"u27", "u28", "u29", "u30", "u31"}, "aggregate"),
    ("用户做过哪些决定", {"u34", "u35"}, "aggregate"),
    ("用户的设计原则", {"u38", "u39", "u41"}, "aggregate"),
]


def _seed(mgr, units):
    for uid, text, kind, imp in units:
        mgr.write(MemoryUnit(id=uid, text=text, kind=kind,
                             importance=imp, scope=MemoryScope.USER))


def _load_embedder():
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except Exception:
        return None
    try:
        model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
    except Exception:
        return None

    class _Emb:
        def encode(self, texts):
            return np.array(model.encode(list(texts), normalize_embeddings=True))

    return _Emb()


def _metrics(retriever, limit=5):
    cat = {}
    r = {1: [], 3: [], 5: []}
    p5, hit1, mrr, n = [], [], [], 0
    for query, gold, category in QUERIES:
        ranked = retriever(query, 5)
        gold = set(gold)
        first = next((i + 1 for i, uid in enumerate(ranked) if uid in gold), 0)
        for k in (1, 3, 5):
            r[k].append(len(set(ranked[:k]) & gold) / len(gold))
        p5.append(len(set(ranked[:5]) & gold) / 5)
        hit1.append(1.0 if first == 1 else 0.0)
        mrr.append(1.0 / first if first else 0.0)
        n += 1
        c = cat.setdefault(category, {"r1": [], "r5": [], "p5": [], "hit1": [], "mrr": []})
        c["r1"].append(r[1][-1]); c["r5"].append(r[5][-1]); c["p5"].append(p5[-1])
        c["hit1"].append(hit1[-1]); c["mrr"].append(mrr[-1])

    avg = lambda xs: sum(xs) / len(xs) if xs else 0.0
    return ({"R@1": avg(r[1]), "R@3": avg(r[3]), "R@5": avg(r[5]),
             "P@5": avg(p5), "Hit@1": avg(hit1), "MRR": avg(mrr), "N": n},
            {k: {"R@1": avg(v["r1"]), "R@5": avg(v["r5"]), "P@5": avg(v["p5"]),
                 "Hit@1": avg(v["hit1"]), "MRR": avg(v["mrr"]), "N": len(v["r1"])}
             for k, v in cat.items()})


def _fmt(name, o, cats):
    print(f"\n[{name}]  N={o['N']}")
    print(f"  R@1={o['R@1']:.1%}  R@3={o['R@3']:.1%}  R@5={o['R@5']:.1%}  "
          f"P@5={o['P@5']:.1%}  Hit@1={o['Hit@1']:.1%}  MRR={o['MRR']:.3f}")
    for c, m in sorted(cats.items()):
        print(f"    {c:<11} R@1={m['R@1']:.0%} R@5={m['R@5']:.0%} Hit@1={m['Hit@1']:.0%} "
              f"MRR={m['MRR']:.2f} (N={m['N']})")


def _rrf(rankings, k):
    scores = {}
    for ranking in rankings:
        for rank, uid in enumerate(ranking):
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return [u for u, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)][:k]


def main():
    storage.reset_db_for_tests()
    from research_agent.memory.tier_b import MemoryManager
    mgr = MemoryManager()
    _seed(mgr, UNITS)
    print(f"Corpus: {len(UNITS)} units ({sum(1 for u in UNITS if 'decoy' in (u[1],))} not tracked)")
    print(f"Queries: {len(QUERIES)}  "
          f"(lexical={sum(1 for q in QUERIES if q[2]=='lexical')}, "
          f"paraphrase={sum(1 for q in QUERIES if q[2]=='paraphrase')}, "
          f"trap={sum(1 for q in QUERIES if q[2]=='trap')}, "
          f"aggregate={sum(1 for q in QUERIES if q[2]=='aggregate')})")

    # L1: keyword-only
    def kw(query, k):
        return [u.id for u in storage.search_keyword(query, limit=k)]
    ok, ck = _metrics(kw)
    _fmt("L1 keyword-only", ok, ck)

    emb = _load_embedder()
    ids, texts, doc_emb = [], [], None
    if emb is not None:
        units = storage.list_units(active_only=True, limit=500)
        ids = [u.id for u in units]
        texts = [u.text for u in units]
        doc_emb = emb.encode(texts) if texts else None

    # L2: hybrid (keyword + vector RRF)
    def hybrid(query, k):
        kwr = [u.id for u in storage.search_keyword(query, limit=k * 3)]
        if doc_emb is None:
            return kwr[:k]
        import numpy as np
        sims = doc_emb @ emb.encode([query])[0]
        vecr = [ids[i] for i in np.argsort(-sims)[: k * 3]]
        return _rrf([kwr, vecr], k)

    if emb is not None:
        oh, ch = _metrics(hybrid)
        _fmt("L2 hybrid (keyword + vector RRF)", oh, ch)
    else:
        print("\n(L2 skipped: embedding model unavailable)")

    # L3: agentic — LLM query expansion → per-variant hybrid → RRF across variants
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("RESEARCH_AGENT_LLM_KEY")
    if emb is not None and api_key:
        import json
        from research_agent.llm import LiteLLMProvider
        llm = LiteLLMProvider(model="deepseek/deepseek-chat", api_key=api_key)

        def expand(query):
            try:
                raw = llm.complete([{"role": "user", "content":
                    "把下面的检索意图改写成3个不同措辞的检索短句（同义、换角度），"
                    "每行一个，不要编号，不要解释：\n" + query}], max_tokens=80, temperature=0)
                variants = [ln.strip(" -•\t") for ln in raw.splitlines() if ln.strip()]
                return [query] + variants[:3]
            except Exception:
                return [query]

        def agentic(query, k):
            rankings = [hybrid(v, k * 2) for v in expand(query)]
            return _rrf(rankings, k)

        cache = {}
        # warm expansion cache (one LLM call per query) and report it
        print("\nRunning LLM query expansion (one call/query)...")
        for q, _, _ in QUERIES:
            cache[q] = expand(q)
        print(f"  sample: {QUERIES[9][0]!r} -> {cache[QUERIES[9][0]]}")

        def agentic_cached(query, k):
            rankings = [hybrid(v, k * 2) for v in cache.get(query, [query])]
            return _rrf(rankings, k)

        oa, ca = _metrics(agentic_cached)
        _fmt("L3 agentic (always expand + hybrid + RRF)", oa, ca)
        if emb is not None:
            print(f"\n  L3 vs L2 hybrid:  R@1 {oa['R@1']-oh['R@1']:+.1%}  "
                  f"R@5 {oa['R@5']-oh['R@5']:+.1%}  MRR {oa['MRR']-oh['MRR']:+.3f}")

        # L4: CONDITIONAL agentic — hybrid first; only expand when confidence is low
        import numpy as np

        def _confidence(query):
            """top-1 cosine similarity as a cheap confidence signal."""
            return float((doc_emb @ emb.encode([query])[0]).max())

        def agentic_conditional(query, k, thr=0.58):
            if _confidence(query) >= thr:
                return hybrid(query, k)          # confident → no rewrite
            rankings = [hybrid(v, k * 2) for v in cache.get(query, [query])]
            return _rrf(rankings, k)

        # report confidence distribution to justify the threshold
        confs = sorted(_confidence(q) for q, _, _ in QUERIES)
        print(f"\n  top-1 cosine confidence: min={confs[0]:.2f} med={confs[len(confs)//2]:.2f} "
              f"max={confs[-1]:.2f}")
        for thr in (0.55, 0.58, 0.62):
            oc, cc = _metrics(lambda q, k, t=thr: agentic_conditional(q, k, t))
            _fmt(f"L4 conditional agentic (expand if conf<{thr})", oc, cc)
            print(f"    vs L2: R@1 {oc['R@1']-oh['R@1']:+.1%}  "
                  f"R@5 {oc['R@5']-oh['R@5']:+.1%}  MRR {oc['MRR']-oh['MRR']:+.3f}")
    else:
        print("\n(L3 skipped: needs DEEPSEEK_API_KEY + embedding model)")


if __name__ == "__main__":
    main()
