# tests/test_brief.py — runtime operating brief (environment + principal + contract)
from research_agent.brief import build_runtime_brief
from research_agent.models import AgentState, Project


def _state(ws, user_input="实现一个 /health 接口", topic="微信机器人"):
    s = AgentState(user_input=user_input)
    s.workspace_dir = ws
    if topic:
        s.active_project = Project(id="p1", topic=topic)
    return s


def test_brief_has_three_sections_and_facts(tmp_path):
    ws = str(tmp_path)
    b = build_runtime_brief(_state(ws), ws)
    assert "[运行环境]" in b and "[工作对象]" in b and "[工作契约]" in b
    assert f"cwd={ws}" in b
    assert "python=" in b and "装包=" in b
    assert "本次目标=实现一个 /health 接口" in b
    assert "完成标准=" in b
    assert "自检=" in b and "测试" in b


def test_brief_principal_includes_project(tmp_path):
    ws = str(tmp_path)
    b = build_runtime_brief(_state(ws, topic="微信机器人"), ws)
    assert "微信机器人" in b
    assert f"workspace={ws}" in b


def test_brief_omits_project_when_absent(tmp_path):
    ws = str(tmp_path)
    s = AgentState(user_input="x")
    s.workspace_dir = ws
    b = build_runtime_brief(s, ws)
    assert "[运行环境]" in b and "项目=" not in b


def test_build_context_injects_brief(tmp_path):
    from research_agent.context import build_context
    s = _state(str(tmp_path))
    msgs = build_context(s, None, "")
    assert msgs[0]["role"] == "system"
    briefs = [m for m in msgs if m["role"] == "system" and "[运行环境]" in m["content"]]
    assert briefs, "runtime brief must be injected"
    assert str(tmp_path) in briefs[0]["content"]
