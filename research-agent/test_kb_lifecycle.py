"""Functional test with file logging."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")

log_path = os.path.join(os.environ.get("TEMP", "."), "pp_e2e_log.txt")
open(log_path, "w").close()  # clear

def log(msg):
    print(msg)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

from research_agent.agent import run_agent
from research_agent.llm import LiteLLMProvider
from research_agent.models import AgentState
import research_agent.agent as ag_mod
ag_mod.MAX_ROUNDS = 4

llm = LiteLLMProvider(model="deepseek/deepseek-chat")

actions = []
def on_event(etype, data):
    t = str(data.get("text", ""))[:100]
    if etype == "step" and data.get("step") in ("init", "route_tools", "route", "thinking"):
        log(f"  [{data['step']}] {t}")
    elif etype == "action":
        actions.append(data.get("action", "?"))
        log(f"  TOOL: {data.get('action')} {str(data.get('query',''))[:80]}")

# ── Test 1: search + read paper ──
log("=" * 50)
log("TEST 1: search arxiv -> read_paper")
log("=" * 50)
state = AgentState(user_input="search arXiv for Attention Is All You Need. Then use read_paper to read the paper you find.")
result = run_agent(state.user_input, llm, state, on_event=on_event)
log(f"Actions: {actions}")
log(f"search_papers: {'search_papers' in actions}, read_paper: {'read_paper' in actions}")
log(f"Response ({len(result.final_response)} chars): {result.final_response[:400]}")

log("")

# ── Test 2: retrieve local ──
log("=" * 50)
log("TEST 2: retrieve local KB")
log("=" * 50)
actions2 = []
def on2(etype, data):
    if etype == "action":
        actions2.append(data.get("action", "?"))
        log(f"  TOOL: {data.get('action')}")
    if etype == "step" and data.get("step") in ("route_tools", "thinking"):
        log(f"  [{data['step']}] {str(data.get('text',''))[:80]}")
state2 = AgentState(user_input="search local knowledge base for papers about attention mechanisms")
result2 = run_agent(state2.user_input, llm, state2, on_event=on2)
log(f"Actions: {actions2}")
log(f"retrieve: {'retrieve' in actions2}")
log(f"Response ({len(result2.final_response)} chars): {result2.final_response[:400]}")

log("")
log("DONE")
