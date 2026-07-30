import sys
sys.path.insert(0, "src")
from research_agent.tools.builtin.retrieve import _handle_search
from research_agent.models import AgentState

state = AgentState(user_input="test")

# Test 1: search returns papers WITHOUT ingestion
print("=== Search: transformer attention ===")
r = _handle_search({"query": "transformer attention"}, None, state, lambda e, d: None)
print(f"success: {r.success}")
print(f"papers found: {r.data.get('found', 0)}")
for p in r.data.get("papers", []):
    print(f"  [{p['year']}] {p['title'][:60]}")
    print(f"        id: {p['paper_id']}")

# Test 2: verify no ingestion happened (no "ingested" in data)
print()
print("=== Ingestion check ===")
if "ingested" in r.data:
    print("FAIL: papers were auto-ingested!")
else:
    print("PASS: no auto-ingestion. papers must be read_paper'd explicitly.")

