"""Quick functional test — single run."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")

from research_agent.agent import run_agent
from research_agent.llm import LiteLLMProvider
from research_agent.models import AgentState
import research_agent.agent as ag_mod
ag_mod.MAX_ROUNDS = 3

llm = LiteLLMProvider(model="deepseek/deepseek-chat")

actions = []
def on_event(etype, data):
    try:
        if etype == "action":
            name = data.get("action", "?")
            query = str(data.get("query", ""))[:80]
            actions.append((name, query))
            print(f"\n[{name}] -> {query}")
        elif etype == "chunk":
            chunk = data.get("text", "")
            # GBK-safe output: replace non-ASCII chars
            safe = chunk.encode("ascii", errors="replace").decode("ascii")
            print(safe[:100], end="", flush=True)
    except Exception:
        pass

print("TASK: say hello")
state = AgentState(user_input="use search_papers to find Attention Is All You Need on arXiv. Then use read_paper to read the paper you found.")
result = run_agent(state.user_input, llm, state, on_event=on_event)
print()
print()
print(f"Actions: {[a[0] for a in actions]}")
print(f"Response ({len(result.final_response)} chars): {result.final_response[:300]}")
