"""Real agent task test — exercises full pipeline with DeepSeek."""
import sys, os, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "")

# Write progress to temp file for debugging
log_path = os.path.join(os.environ.get("TEMP", "."), "pp_test_log.txt")
def log(msg):
    print(msg, flush=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

log("=== Starting test ===")

from research_agent.agent import run_agent
import research_agent.agent as ag_mod
ag_mod.MAX_ROUNDS = 3
from research_agent.llm import LiteLLMProvider
from research_agent.models import AgentState

llm = LiteLLMProvider(model="deepseek/deepseek-chat")

actions = []
def on_event(etype, data):
    if etype == "action":
        name = data.get("action", "?")
        actions.append(name)
        log(f"  TOOL: {name} <- {str(data.get('query', ''))[:80]}")
    elif etype == "step":
        step = data.get("step", "")
        text = data.get("text", "")[:120]
        if step:
            log(f"  STEP[{step}]: {text}")
        elif text:
            log(f"  STEP: {text}")
    elif etype == "tool":
        status = str(data.get("status", ""))[:80]
        hint = str(data.get("hint", ""))[:80]
        if status or hint:
            log(f"  STATUS: {status} | {hint}")
    elif etype == "chunk":
        log(f"  CHUNK", data.get("text", "")[:50])

log("TASK: search arxiv for attention papers, read best one")
state = AgentState(user_input="search arxiv for attention is all you need paper. Then use read_paper to read its full text. I need the complete methodology details, not just the abstract.")

try:
    result = run_agent(state.user_input, llm, state, on_event=on_event)
    log(f"Done. actions={actions}, response_len={len(result.final_response)}")
    log(f"HAS search: {'search_papers' in actions}, HAS read: {'read_paper' in actions}")
    log(f"Response: {result.final_response[:300]}")
except Exception as e:
    log(f"ERROR: {e}")
    log(traceback.format_exc())


