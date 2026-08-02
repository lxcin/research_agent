"""End-to-end functional test: search, read, retrieve, KB lifecycle."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")

# ── Setup ──
from research_agent.agent import run_agent
from research_agent.llm import LiteLLMProvider
from research_agent.models import AgentState
import research_agent.agent as ag_mod
ag_mod.MAX_ROUNDS = 4
llm = LiteLLMProvider(model="deepseek/deepseek-chat")

def run_task(task, label):
    actions = []
    text_chunks = []
    def on_event(etype, data):
        if etype == "action":
            actions.append(data.get("action", "?"))
        if etype == "chunk":
            text_chunks.append((data.get("text", ""),))
    state = AgentState(user_input=task)
    result = run_agent(task, llm, state, on_event=on_event)
    return {
        "label": label,
        "actions": actions,
        "response_len": len(result.final_response),
        "response_head": result.final_response[:300],
    }

# ── Test 1: search_papers (arXiv, no ingestion) ──
print("=" * 60)
print("TEST 1: search_papers on arXiv — no ingestion")
print("=" * 60)
r1 = run_task(
    "search arXiv for 'Attention Is All You Need' paper and tell me the title and authors",
    "search_arxiv"
)
print(f"  Actions: {r1['actions']}")
print(f"  Response ({r1['response_len']} chars): {r1['response_head'][:150]}...")
has_search = "search_papers" in r1["actions"]
has_read = "read_paper" in r1["actions"]
print(f"  {'PASS' if has_search else 'FAIL'}: search_papers called")
print(f"  {('PASS' if not has_read else 'INFO')}: read_paper {'NOT' if not has_read else ''} called (no ingestion on search)")

# ── Test 2: read_paper on a paper NOT yet ingested ──
print()
print("=" * 60)
print("TEST 2: read_paper on arXiv paper — first read")
print("=" * 60)
r2 = run_task(
    "use read_paper to read the paper with arxiv ID 1706.03762 (Attention Is All You Need). Read its full text.",
    "read_new"
)
print(f"  Actions: {r2['actions']}")
print(f"  Response ({r2['response_len']} chars): {r2['response_head'][:150]}...")
has_read2 = "read_paper" in r2["actions"]
print(f"  {'PASS' if has_read2 else 'FAIL'}: read_paper called on arXiv paper")

# ── Test 3: read_paper AGAIN — should get full text from KB ──
print()
print("=" * 60)
print("TEST 3: read_paper on same paper — should hit local KB")
print("=" * 60)
r3 = run_task(
    "read_paper for paper ID 1706.03762 — read its full text again",
    "read_cached"
)
print(f"  Actions: {r3['actions']}")
print(f"  Response ({r3['response_len']} chars): {r3['response_head'][:150]}...")
has_read3 = "read_paper" in r3["actions"]
print(f"  {'PASS' if has_read3 else 'FAIL'}: read_paper called on cached paper")

# ── Test 4: retrieve from local KB ──
print()
print("=" * 60)
print("TEST 4: retrieve from local knowledge base")
print("=" * 60)
r4 = run_task(
    "检索本地知识库中关于attention机制或transformer的论文",
    "retrieve_local"
)
print(f"  Actions: {r4['actions']}")
print(f"  Response ({r4['response_len']} chars): {r4['response_head'][:150]}...")
has_retrieve = "retrieve" in r4["actions"]
print(f"  {'PASS' if has_retrieve else 'FAIL'}: retrieve called to search local KB")

# ── Test 5: delete_paper cleanup ──
print()
print("=" * 60)
print("TEST 5: delete_paper cleanup")
print("=" * 60)
r5 = run_task(
    "delete the paper with ID 1706.03762 from the knowledge base",
    "delete_paper"
)
print(f"  Actions: {r5['actions']}")
print(f"  Response: {r5['response_head'][:150]}...")
has_delete = "delete_paper" in r5["actions"]
print(f"  {'PASS' if has_delete else 'INFO'}: delete_paper called")

# ── Summary ──
print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)
for r in [r1, r2, r3, r4, r5]:
    print(f"  {r['label']:20s} | actions={r['actions']} | {r['response_len']}chars")
