"""Real agent task test — exercises full pipeline with DeepSeek."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")

from research_agent.agent import run_agent
from research_agent.llm import LiteLLMProvider
from research_agent.models import AgentState

# Limit rounds for faster testing
import research_agent.agent as ag_mod
ag_mod.MAX_ROUNDS = 3

llm = LiteLLMProvider(model="deepseek/deepseek-chat")

def on_event(etype, data):
    if etype == "chunk":
        print(data.get("text", ""), end="", flush=True)
    elif etype == "action":
        print(f"\n[TOOL] {data.get('action', '?')}: {data.get('query', '')[:100]}")
    elif etype == "step":
        step = data.get("step", "")
        text = data.get("text", "")[:120]
        if step in ("init", "route_tools", "project_created"):
            print(f"\n[STEP] {text}")
    elif etype == "tool":
        hint = data.get("hint", "")
        if hint:
            print(f"\n[HINT] {hint}")

print("=" * 60)
print("TASK: 搜索arXiv上2026年关于Transformer的最新论文")
print("=" * 60)

state = AgentState(user_input="在arXiv搜索2026年Transformer架构的最新论文，列出3篇标题")
result = run_agent(state.user_input, llm, state, on_event=on_event)

print()
print()
print(f"OUTPUT: {len(result.final_response)} chars")
print(result.final_response[:800])

