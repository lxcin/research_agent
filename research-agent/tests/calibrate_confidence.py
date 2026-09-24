"""Calibrate memory.confidence_strong / confidence_weak from the top-1 cosine
distribution of PRESENT vs ABSENT queries.

PRESENT = the 27 labeled queries from eval_memory_agentic (answer is in corpus).
ABSENT  = hand-written queries whose facts are NOT in the corpus.
No LLM / network: pure local bge cosine.

Run:
  PYTHONPATH=src HF_HUB_OFFLINE=1 python tests/calibrate_confidence.py
"""
import os, sys
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, "tests")
import numpy as np  # noqa: E402
from eval_memory_agentic import UNITS, QUERIES  # noqa: E402

ABSENT = [
    "用户喜欢什么颜色的车", "用户会做什么拿手菜", "用户有几个孩子", "用户开什么牌子手机",
    "用户的医保卡号是多少", "用户去过哪些国家旅游", "用户最喜欢的电影", "用户的星座",
    "用户小时候的绰号", "用户会不会游泳", "用户的房贷利率", "用户养了几只狗",
    "用户上一份工作在哪", "用户的血型", "用户喜欢喝什么咖啡", "用户的驾照什么时候到期",
    "用户最讨厌的食物", "用户平时几点睡觉", "用户的银行卡余额", "用户住在哪个小区",
    "用户有没有兄弟姐妹", "用户的高考分数", "用户喜欢哪支球队", "用户的过敏史",
    "用户用什么相机", "用户的房贷还了几年", "用户喜欢什么音乐", "用户的鞋码",
]


def main():
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
    texts = [u[1] for u in UNITS]
    D = np.array(m.encode(texts, normalize_embeddings=True))

    def feats(qs):
        qv = np.array(m.encode(list(qs), normalize_embeddings=True))
        S = qv @ D.T
        srt = np.sort(S, axis=1)[:, ::-1]
        t1 = srt[:, 0]
        return {
            "top1": t1,
            "margin_t1t2": t1 - srt[:, 1],
            "margin_top5": t1 - srt[:, :5].mean(axis=1),
            "z": (t1 - S.mean(axis=1)) / (S.std(axis=1) + 1e-9),
        }

    present = feats([q for q, _, _ in QUERIES])
    absent = feats(ABSENT)

    print("=== feature separability (best balanced-accuracy threshold) ===")
    print(f"{'feature':<14}{'tau':>7}{'present_recall':>16}{'absent_below':>14}{'bal_acc':>9}")
    for k in present:
        ps, as_ = present[k], absent[k]
        taus = np.round(np.arange(min(ps.min(), as_.min()), max(ps.max(), as_.max()), 0.005), 4)
        best = max(taus, key=lambda t: ((ps >= t).mean() + (as_ < t).mean()) / 2)
        pr = (ps >= best).mean(); ab = (as_ < best).mean()
        print(f"{k:<14}{best:>7.3f}{pr:>16.0%}{ab:>14.0%}{(pr+ab)/2:>9.0%}")

    print("\n=== raw top1 sweep (for reference) ===")
    print(f"{'tau':>6}{'present_recall':>16}{'absent_FP':>12}")
    for tau in [0.50, 0.55, 0.58, 0.60, 0.62, 0.65, 0.68]:
        print(f"{tau:>6.2f}{(present['top1']>=tau).mean():>16.0%}{(absent['top1']>=tau).mean():>12.0%}")

    print("\n=== margin_top5 sweep ===")
    print(f"{'tau':>6}{'present_recall':>16}{'absent_FP':>12}")
    for tau in [0.02, 0.04, 0.06, 0.08, 0.10, 0.12]:
        print(f"{tau:>6.2f}{(present['margin_top5']>=tau).mean():>16.0%}"
              f"{(absent['margin_top5']>=tau).mean():>12.0%}")

    print("\n=== query-side bge retrieval instruction ===")
    INSTR = "为这个句子生成表示以用于检索相关文章："

    def top1_pref(qs):
        qv = np.array(m.encode([INSTR + q for q in qs], normalize_embeddings=True))
        return (qv @ D.T).max(axis=1)
    p2 = top1_pref([q for q, _, _ in QUERIES])
    a2 = top1_pref(ABSENT)
    taus = np.round(np.arange(0.30, 0.95, 0.005), 4)
    best = max(taus, key=lambda t: ((p2 >= t).mean() + (a2 < t).mean()) / 2)
    print(f"best tau={best:.3f} present_recall={(p2>=best).mean():.0%} "
          f"absent_below={(a2<best).mean():.0%} "
          f"bal_acc={((p2>=best).mean()+(a2<best).mean())/2:.0%}")
    print(f"{'tau':>6}{'present_recall':>16}{'absent_FP':>12}")
    for tau in [0.55, 0.60, 0.65, 0.70, 0.75]:
        print(f"{tau:>6.2f}{(p2>=tau).mean():>16.0%}{(a2>=tau).mean():>12.0%}")


if __name__ == "__main__":
    main()
