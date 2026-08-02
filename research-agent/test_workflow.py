"""Integration test: verify LLM uses search_papers → read_paper workflow."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")

from research_agent.agent import run_agent
from research_agent.llm import LiteLLMProvider
from research_agent.models import AgentState
import research_agent.agent as ag_mod
ag_mod.MAX_ROUNDS = 4

llm = LiteLLMProvider(model="deepseek/deepseek-chat")

actions = []
def on_event(etype, data):
    if etype == "chunk":
        print(data.get("text", ""), end="", flush=True)
    elif etype == "action":
        name = data.get("action", "?")
        query = data.get("query", "")[:100]
        actions.append(name)
        print(f"\n[ACT {len(actions)}] {name}")
    elif etype == "step":
        text = data.get("text", "")[:100]
        if text:
            print(f"  > {text}")
    elif etype == "tool":
        hint = data.get("hint", "")[:100]
        status = str(data.get("status", ""))
        if "pdf" in status.lower():
            print(f"  [PDF] {status}")

print("=" * 70)
print("TEST: search then read paper workflow")
print("=" * 70)
state = AgentState(user_input="搜索arXiv上标题包含Transformer的2026年论文，选一篇最有价值的，用read_paper读它的全文（我需要的是完整的论文内容，不是摘要）")
result = run_agent(state.user_input, llm, state, on_event=on_event)
print()
print()
print(f"Actions: {actions}")
has_read = "read_paper" in actions
has_search = "search_papers" in actions  
print(f"search_papers: {has_search}, read_paper: {has_read}")
print(f"{'PASS' if has_search and has_read else 'NO READ'} — LLM {'called' if has_read else 'skipped'} read_paper")
print()
print(f"Response ({len(result.final_response)} chars):")
print(result.final_response[:500])

