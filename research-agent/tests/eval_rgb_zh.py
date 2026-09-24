"""RGB (中文) 鲁棒性评测 —— 端到端 检索 + 生成 + LLM 评判。

官方 RGB 是"给定上下文、只测生成器"；本脚本改为端到端：
本地 bge 向量召回 + MMR 重排 → 生成答案 → DeepSeek 评判。
因此数值**不可与 RGB 榜单直接比较**，只用于衡量本框架的 agentic-RAG 路径。

能力 (数据来自 https://github.com/chen700564/RGB, CC BY-NC-SA 4.0):
  noise  rgb_zh.json        正+负文档    → 答案准确率 + 正例召回
  refine rgb_zh_refine.json 仅负文档    → 拒答率（Rej*）
  int    rgb_zh_int.json    多正文档整合 → 准确率
  fact   rgb_zh_fact.json   含反事实文档 → 准确率 + 错误检出（不被误导）

用法:
  PYTHONPATH=src DEEPSEEK_API_KEY=... python tests/eval_rgb_zh.py --n 20
  PYTHONPATH=src DEEPSEEK_API_KEY=... python tests/eval_rgb_zh.py --abilities noise,refine --n 30
数据目录: tests/_data/rgb （可用 RGB_DATA_DIR 覆盖）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np  # noqa: E402

from research_agent.memory.rerank import mmr_select  # noqa: E402

DATA_DIR = Path(os.environ.get("RGB_DATA_DIR", Path(__file__).parent / "_data" / "rgb"))
FILES = {"noise": "rgb_zh.json", "refine": "rgb_zh_refine.json",
         "int": "rgb_zh_int.json", "fact": "rgb_zh_fact.json"}
K = 5
POOL = 20
LAM = 0.7
DOC_CHARS = 400


def load(name, n):
    path = DATA_DIR / FILES[name]
    rows = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            rows.append(json.loads(line))
    return rows[:n]


def _load_embedder():
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)

    def enc(texts):
        return np.array(model.encode(list(texts), normalize_embeddings=True))
    return enc


def _as_text(x):
    if isinstance(x, (list, tuple)):
        return " ".join(_as_text(i) for i in x)
    return str(x)


def _docs_for(ability, row):
    """Build the retrieval pool (list[str])."""
    pos = row.get("positive", []) or []
    neg = row.get("negative", []) or []
    if ability == "refine":
        # negative rejection: corpus deliberately has NO relevant doc
        docs = list(neg)
    elif ability == "fact":
        docs = pos + (row.get("positive_wrong", []) or []) + neg
    else:
        docs = pos + neg
    return [_as_text(d) for d in docs]


def retrieve(enc, query, docs, k=K, lam=LAM):
    if not docs:
        return [], []
    dtexts = [d[:DOC_CHARS] for d in docs]
    D = enc(dtexts)
    qv = enc([query])[0]
    sims = D @ qv
    order = list(np.argsort(-sims)[:POOL])
    cids = [str(i) for i in order]
    picked = mmr_select(qv, D[order], cids, min(k, len(order)), lam=lam)
    idx = [int(i) for i in picked]
    return [dtexts[i] for i in idx], idx


GEN_PROMPT = """你是严谨的问答助手。只根据下面提供的资料回答问题。
资料中没有相关信息时，只输出「无法回答」四个字，不要编造、不要解释。
资料：
{ctx}
问题：{q}
回答："""

JUDGE_PROMPT = """判断「候选回答」是否正确回答了问题（与参考答案语义一致即可，数字/单位必须一致）。
若候选回答表示无法回答/信息不足，判定为 abstain。
只输出一个词：correct / incorrect / abstain
问题：{q}
参考答案：{gold}
候选回答：{ans}"""


def gen_answer(llm, question, ctx_docs):
    ctx = "\n".join(f"[{i+1}] {d}" for i, d in enumerate(ctx_docs)) or "（无资料）"
    raw = llm.complete([{"role": "user", "content": GEN_PROMPT.format(ctx=ctx, q=question)}],
                       max_tokens=80, temperature=0)
    return (raw or "").strip()


def judge(llm, question, gold, ans):
    raw = llm.complete([{"role": "user", "content": JUDGE_PROMPT.format(
        q=question, gold=" / ".join(_as_text(g) for g in gold), ans=ans)}],
        max_tokens=10, temperature=0)
    v = (raw or "").strip().lower()
    for tag in ("correct", "incorrect", "abstain"):
        if tag in v:
            return tag
    return "incorrect"


def eval_ability(ability, rows, llm, enc, k=K, lam=LAM):
    n = len(rows)
    correct = abstain = 0
    pos_total = pos_hit = 0
    for row in rows:
        q = row["query"]
        gold = row.get("answer", []) or []
        docs = _docs_for(ability, row)
        ctx_docs, idx = retrieve(enc, q, docs, k=k, lam=lam)
        # positive recall: positives are placed first in the pool
        pos = row.get("positive", []) or []
        if ability != "refine" and pos:
            pos_total += len(pos)
            pos_hit += sum(1 for i in idx if i < len(pos))
        ans = gen_answer(llm, q, ctx_docs)
        verdict = judge(llm, q, gold, ans)
        if verdict == "correct":
            correct += 1
        elif verdict == "abstain":
            abstain += 1
    return dict(n=n, correct=correct, abstain=abstain,
                pos_recall=round(pos_hit / pos_total, 3) if pos_total else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="samples per ability")
    ap.add_argument("--abilities", default="noise,refine,int,fact")
    ap.add_argument("--k", type=int, default=K, help="retrieved passages")
    ap.add_argument("--lam", type=float, default=LAM, help="MMR lambda (1.0 = no MMR)")
    ap.add_argument("--out", default=str(Path(__file__).parent / "_data" / "rgb_results.json"))
    args = ap.parse_args()

    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("RESEARCH_AGENT_LLM_KEY")
    if not key:
        print("ERROR: DEEPSEEK_API_KEY not set"); sys.exit(1)
    from research_agent.llm import LiteLLMProvider
    llm = LiteLLMProvider(model="deepseek/deepseek-chat", api_key=key)
    enc = _load_embedder()

    abilities = [a.strip() for a in args.abilities.split(",") if a.strip()]
    summary = {}
    for ab in abilities:
        rows = load(ab, args.n)
        t0 = time.time()
        res = eval_ability(ab, rows, llm, enc, k=args.k, lam=args.lam)
        res["secs"] = round(time.time() - t0, 1)
        summary[ab] = res
        # headline metric per ability
        pr = res.get("pos_recall")
        pr_s = f" 正例召回={pr:.0%}" if pr is not None else ""
        if ab == "refine":
            print(f"[{ab}] n={res['n']} 拒答率(Rej*)={res['abstain']/res['n']:.0%} "
                  f"误答={1-res['abstain']/res['n']:.0%} ({res['secs']}s)", flush=True)
        else:
            print(f"[{ab}] n={res['n']} 准确率={res['correct']/res['n']:.0%}{pr_s} "
                  f"(abstain={res['abstain']}) ({res['secs']}s)", flush=True)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(summary, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nsaved -> {args.out}")


if __name__ == "__main__":
    main()
