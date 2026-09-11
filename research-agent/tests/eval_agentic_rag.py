"""Conversation-level agentic-RAG evaluation (real LLM, real agent loop).

Unlike eval_memory_*.py (which tests the RETRIEVER on a fixed query), this tests
the AGENT: given a multi-turn conversation, does it decide to consult long-term
memory, does it FORMULATE a good query from the dialogue, and does it produce a
grounded answer?

Two conditions per scenario:
  A) memory tool DISABLED  -> baseline (agent cannot retrieve; may guess/hallucinate)
  B) memory tool ENABLED   -> agentic (agent may call search_memory; guided by prompt)

Metrics (from the captured event stream + final answer):
  should_retrieve scenarios:
    decision   : fraction where search_memory was called
    query_hit  : fraction where the agent's ACTUAL query retrieves a gold unit (top5)
    grounding  : fraction where the final answer contains a gold distinctive token
  should_not_retrieve scenarios:
    over_retrieval : fraction where search_memory was (unnecessarily) called

Run:
    PYTHONPATH=src DEEPSEEK_API_KEY=... python tests/eval_agentic_rag.py
"""
import os
import tempfile

os.environ.setdefault("RESEARCH_AGENT_DATA_DIR", tempfile.mkdtemp(prefix="agentic_rag_"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ["RESEARCH_AGENT_TEMPERATURE"] = "0"
os.environ["RESEARCH_AGENT_MAX_ROUNDS"] = "6"

from research_agent.memory import storage  # noqa: E402
from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope  # noqa: E402
from research_agent.memory.tier_b import MemoryManager  # noqa: E402


# ── Scenarios ───────────────────────────────────────────────────────────────
# Each scenario seeds gold memory, optionally with prior conversation turns
# (to exercise reference/ellipsis). `tokens` are distinctive strings that should
# only appear in a grounded answer.
SCENARIOS = [
    dict(
        name="mentor_name",
        gold=[("g1", "用户的导师叫王立群，研究方向是信息检索", MemoryKind.FACT, 0.9)],
        history=[],
        current="我导师叫什么名字来着？",
        should_retrieve=True, tokens=["王立群"],
    ),
    dict(
        name="why_drop_langgraph",
        gold=[("g2", "用户曾放弃 LangGraph，因为多轮循环里的异常堆栈(traceback)很难定位", MemoryKind.DEAD_END, 0.8)],
        history=[],
        current="我之前为什么放弃用 LangGraph？",
        should_retrieve=True, tokens=["traceback", "堆栈", "调试"],
    ),
    dict(
        name="thesis_topic",
        gold=[("g3", "用户的本科毕业设计题目是《基于对比学习的跨模态检索》", MemoryKind.FACT, 0.9)],
        history=[],
        current="我的毕设题目是什么？",
        should_retrieve=True, tokens=["对比学习", "跨模态"],
    ),
    dict(
        name="data_location",
        gold=[("g4", "用户把实验数据统一放在 D 盘的 experiment_data 目录", MemoryKind.FACT, 0.7)],
        history=[],
        current="我的实验数据一般放在哪？",
        should_retrieve=True, tokens=["experiment_data"],
    ),
    dict(
        name="reference_ellipsis",
        gold=[("g5", "用户打算用 THUCNews 数据集做中文分类评测", MemoryKind.FACT, 0.7)],
        history=[
            ("我想评测一下不同分词器在中文分类上的效果", "好的，准备用哪个数据集？"),
            ("我打算用 THUCNews 这个数据集", "了解了，那之后就用它。"),
        ],
        current="我上次说要用的那个评测数据集叫什么来着？",
        should_retrieve=True, tokens=["THUCNews"],
    ),
    dict(
        name="config_change",
        gold=[("g6", "用户把 config.yml 里的 max_tokens 从 4000 改成了 12000", MemoryKind.DECISION, 0.7)],
        history=[],
        current="我之前改过一个配置，改的是什么？",
        should_retrieve=True, tokens=["max_tokens", "12000"],
    ),
    # should NOT need memory
    dict(
        name="general_coding",
        gold=[],
        history=[],
        current="用 Python 写一个快速排序函数。",
        should_retrieve=False, tokens=[],
    ),
    dict(
        name="general_concept",
        gold=[],
        history=[],
        current="解释一下 Transformer 的自注意力机制是什么。",
        should_retrieve=False, tokens=[],
    ),
]


def _seed(mgr, gold):
    for uid, text, kind, imp in gold:
        mgr.write(MemoryUnit(id=uid, text=text, kind=kind, importance=imp,
                             scope=MemoryScope.USER))


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


RRF_K = 60


def _query_hits_gold(query, gold_ids, emb):
    """Does the agent's query retrieve any gold unit in top-5? (keyword+vector RRF)"""
    kw = [u.id for u in storage.search_keyword(query, limit=15)]
    if emb is None:
        return bool(set(kw[:5]) & set(gold_ids))
    import numpy as np
    units = storage.list_units(active_only=True, limit=500)
    ids = [u.id for u in units]
    texts = [u.text for u in units]
    doc = emb.encode(texts)
    sims = doc @ emb.encode([query])[0]
    vec = [ids[i] for i in np.argsort(-sims)[:15]]
    scores = {}
    for rank, uid in enumerate(kw):
        scores[uid] = scores.get(uid, 0) + 1 / (RRF_K + rank + 1)
    for rank, uid in enumerate(vec):
        scores[uid] = scores.get(uid, 0) + 1 / (RRF_K + rank + 1)
    top5 = [u for u, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)][:5]
    return bool(set(top5) & set(gold_ids))


def _run_one(scenario, memory_on, emb):
    """Run the real agent for one scenario. Returns dict of observed behavior."""
    from research_agent.agent import AgentState, run_agent
    from research_agent.llm import LiteLLMProvider
    from research_agent import project_manager as pm
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins

    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("RESEARCH_AGENT_LLM_KEY")
    llm = LiteLLMProvider(model="deepseek/deepseek-chat", api_key=key)

    # memory DB: fresh + seed gold
    storage.reset_db_for_tests()
    if scenario["gold"]:
        _seed(MemoryManager(), scenario["gold"])

    # toggle memory plugin
    register_builtins()
    reg = get_registry()
    if memory_on:
        reg.enable_plugin("memory")
    else:
        reg.disable_plugin("memory")

    # temp workspace + chat + history turns
    ws = tempfile.mkdtemp()
    pm.init_project(ws, topic="eval")
    chat_id = pm.create_chat(ws, title="eval")
    from research_agent.memory import store_turn
    for i, (u, a) in enumerate(scenario.get("history", []), 1):
        store_turn(ws, chat_id, i, u, a)

    events = []
    def on_event(et, d):
        events.append((et, d))

    state = AgentState()
    result = run_agent(scenario["current"], llm, state, on_event=on_event,
                       workspace_dir=ws, chat_id=chat_id)

    calls = [d.get("input", {}).get("query", "")
             for et, d in events
             if et == "tool_start" and d.get("name") == "search_memory"]
    answer = result.final_response or ""
    gold_ids = [g[0] for g in scenario["gold"]]
    hit = bool(calls) and _query_hits_gold(calls[0], gold_ids, emb) if gold_ids else None
    grounded = any(tok.lower() in answer.lower() for tok in scenario["tokens"]) if scenario["tokens"] else None
    return {"called": bool(calls), "queries": calls, "query_hit": hit,
            "grounded": grounded, "answer": answer}


def main():
    emb = _load_embedder()
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("RESEARCH_AGENT_LLM_KEY")
    if not key:
        print("ERROR: need DEEPSEEK_API_KEY for the real-LLM agentic eval.")
        return
    print(f"Agentic-RAG eval: {len(SCENARIOS)} scenarios | "
          f"embedder={'on' if emb else 'off'}")

    rows = []
    for sc in SCENARIOS:
        a = _run_one(sc, memory_on=False, emb=emb)
        b = _run_one(sc, memory_on=True, emb=emb)
        rows.append((sc, a, b))
        tag = "RETRIEVE" if sc["should_retrieve"] else "NO-RETRIEVE"
        print(f"\n[{sc['name']}] ({tag})  Q: {sc['current']}")
        print(f"  A(no mem) called={a['called']}  grounded={a['grounded']}")
        print(f"  B(withmem) called={b['called']} queries={b['queries'][:1]} "
              f"hit={b['query_hit']} grounded={b['grounded']}")
        print(f"  A ans: {a['answer'][:90].replace(chr(10),' ')}")
        print(f"  B ans: {b['answer'][:90].replace(chr(10),' ')}")

    # aggregate
    ret = [r for r in rows if r[0]["should_retrieve"]]
    noret = [r for r in rows if not r[0]["should_retrieve"]]
    def rate(xs):
        return (sum(1 for x in xs if x) / len(xs)) if xs else 0.0

    print("\n" + "=" * 60)
    print("AGGREGATE")
    if ret:
        print(f"  should-retrieve (n={len(ret)}):")
        print(f"    decision  (B called search_memory): {rate([r[2]['called'] for r in ret]):.0%}")
        print(f"    query_hit (B query -> gold top5)  : {rate([r[2]['query_hit'] for r in ret]):.0%}")
        print(f"    grounding A (no mem)              : {rate([r[1]['grounded'] for r in ret]):.0%}")
        print(f"    grounding B (with mem)            : {rate([r[2]['grounded'] for r in ret]):.0%}")
        print(f"    grounding gain (B-A)              : "
              f"{rate([r[2]['grounded'] for r in ret]) - rate([r[1]['grounded'] for r in ret]):+.0%}")
    if noret:
        print(f"  should-NOT-retrieve (n={len(noret)}):")
        print(f"    over-retrieval (A) : {rate([r[1]['called'] for r in noret]):.0%}")
        print(f"    over-retrieval (B) : {rate([r[2]['called'] for r in noret]):.0%}")


if __name__ == "__main__":
    main()
