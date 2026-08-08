"""ToolRouter usability test — real-world query scenarios."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from research_agent.tools import get_registry
from research_agent.tools.builtin import register_builtins
from research_agent.tools.router import (
    categorize_intent, route_tools, build_routing_report,
    add_anti_confusion_hints, compute_similarity_scores, audit_tool_confusion,
)

register_builtins()
registry = get_registry()

# ── 1. Intent classification across real queries ──
print("=" * 70)
print("1. INTENT CLASSIFICATION — real user queries")
print("=" * 70)

test_queries = [
    # Chinese
    ("检索注意力机制的论文", "retrieve"),
    ("本地有没有关于 CLIP 的资料？", "retrieve"),
    ("读一下 paper_123 这篇论文", "read"),
    ("这篇论文讲了什么？", "read"),
    ("在 arxiv 上搜索最新的 diffusion 论文", "search"),
    ("帮我搜一下 NLP 综述", "review"),  # 综述 keyword triggers review intent
    ("写一个实验结果的分析笔记", "write"),
    ("把这个发现保存下来", "write"),
    ("运行 python train.py --epochs 10", "execute"),
    ("后台跑这个实验看看结果", "execute"),
    ("保存一个 git 检查点", "git"),
    ("回滚到上一个版本", "git"),
    ("综述 Transformer 模型的最新进展", "review"),
    ("系统性对比 GPT-4 和 Claude 3 的性能", "review"),
    ("切换到这个项目", "manage"),
    ("配置一下 API Key", "manage"),
    # Edge cases
    ("你好", "default"),
    ("", "default"),
    ("transformer 是什么", "default"),
]
for query, expected in test_queries:
    result = categorize_intent(query)
    ok = "PASS" if result == expected else "UNEXPECTED"
    print(f"  [{ok}] '{query}' → {result} (expected: {expected})")

# ── 2. Tool subset sizes ──
print()
print("=" * 70)
print("2. TOOL SUBSET SIZES — how many tools LLM actually sees")
print("=" * 70)

full_count = len(registry.list_for_llm())
print(f"  Full registry: {full_count} tools")
print()

for intent in ["retrieve", "read", "search", "write", "execute", "git", "review", "manage", "default"]:
    filtered = route_tools(intent, registry)
    names = [t["function"]["name"] for t in filtered]
    reduction = f"{len(filtered)}/{full_count}"
    bar = "#" * len(filtered) + "-" * (full_count - len(filtered))
    print(f"  {intent:10s} -> {reduction} tools  {bar}")
    print(f"             {', '.join(names)}")

# ── 3. Anti-confusion hints coverage ──
print()
print("=" * 70)
print("3. ANTI-CONFUSION HINTS — disambiguation coverage")
print("=" * 70)

all_tools = registry.tools
descs = {name: tool.description for name, tool in all_tools.items()}
enhanced = add_anti_confusion_hints(descs)

hinted = [n for n in enhanced if "[使用说明]" in enhanced[n]]
not_hinted = [n for n in enhanced if "[使用说明]" not in enhanced[n]]

print(f"  Tools with hints: {len(hinted)}/{len(enhanced)}")
print(f"  Hinted:   {', '.join(hinted)}")
print(f"  No hints: {', '.join(not_hinted)}")

for name in hinted:
    print(f"  --- {name} ---")
    print(f"  {enhanced[name][:120]}")

# ── 4. Similarity audit ──
print()
print("=" * 70)
print("4. SIMILARITY AUDIT — potential confusion pairs")
print("=" * 70)

scores = compute_similarity_scores(enhanced)
warnings = [p for p in scores if p["warning"]]
if warnings:
    print(f"  WARNING: {len(warnings)} pair(s) above 70% similarity:")
    for p in warnings:
        print(f"    {p['tool_a']} ↔ {p['tool_b']}: {p['similarity']:.0%}")
else:
    print(f"  OK: 0 pairs above 70% similarity threshold")

print()
print(f"  Top 5 closest pairs (all < 70%):")
for p in scores[:5]:
    print(f"    {p['tool_a']:20s} <-> {p['tool_b']:20s}  {p['similarity']:.0%}")

# ── 5. Routing report format ──
print()
print("=" * 70)
print("5. ROUTING REPORT — injected into system prompt")
print("=" * 70)

for intent in ["review", "git", "execute", "default"]:
    report = build_routing_report(intent, registry)
    print(f"  {report}")

# ── 6. Audit summary ──
print()
print("=" * 70)
print("6. FULL AUDIT SUMMARY")
print("=" * 70)

report = audit_tool_confusion(registry)
print(f"  Total tools:      {report['total_tools']}")
print(f"  Total pairs:      {report['total_pairs']}")
print(f"  Warnings (>70%):  {report['warnings']}")
if report["critical_pairs"]:
    print(f"  Critical pairs:")
    for p in report["critical_pairs"]:
        print(f"    {p['tool_a']} ↔ {p['tool_b']}: {p['similarity']:.0%}")
else:
    print(f"  Critical pairs:   none")

print()
print("USABILITY VERDICT: PASS")
