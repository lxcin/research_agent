# tests/test_plan.py — task planning (plan-as-memory): model, store, tools, plugin
import types

import pytest

from research_agent import plan as plan_mod
from research_agent.models import AgentState


def _ws():
    return "E:/tmp/plan-ws"


# ── data model ──

def test_plan_roundtrip_and_render():
    p = plan_mod.Plan(goal="部署服务",
                      steps=[plan_mod.PlanStep("构建"), plan_mod.PlanStep("上线", status="done")])
    d = p.to_dict()
    p2 = plan_mod.Plan.from_dict(d)
    assert p2.goal == "部署服务"
    assert [s.step for s in p2.steps] == ["构建", "上线"]
    assert p2.steps[1].status == "done"
    text = p2.render()
    assert "部署服务" in text and "[ ]" in text and "[x]" in text


# ── persistence + recall ──

def test_save_and_list_plans(tmp_path):
    ws = str(tmp_path)
    plan_mod.save_plan(plan_mod.Plan(goal="任务A", workspace_dir=ws,
                                     steps=[plan_mod.PlanStep("步骤1")]))
    plans = plan_mod.list_plans(ws)
    assert len(plans) == 1 and plans[0].goal == "任务A"


def test_episodes_stored_in_sqlite_db(tmp_path):
    from research_agent.config import get_data_dir
    ws = str(tmp_path)
    pid = plan_mod.save_plan(plan_mod.Plan(goal="任务A", workspace_dir=ws))
    assert pid                                   # row id returned
    assert (get_data_dir() / "episodes.db").is_file()   # one DB file, not JSONs
    assert [p.goal for p in plan_mod.list_all_plans()] == ["任务A"]


def test_search_plans_by_keyword(tmp_path):
    ws = str(tmp_path)
    plan_mod.save_plan(plan_mod.Plan(
        goal="部署 Docker 服务", workspace_dir=ws,
        steps=[plan_mod.PlanStep("编写 Dockerfile"), plan_mod.PlanStep("docker build")]))
    plan_mod.save_plan(plan_mod.Plan(
        goal="写一份周报", workspace_dir=ws, steps=[plan_mod.PlanStep("汇总本周工作")]))

    hits = plan_mod.search_plans(ws, "docker build 部署")
    assert hits and hits[0].goal == "部署 Docker 服务"
    assert plan_mod.search_plans(ws, "完全无关的词汇xyz") == []


def test_search_plans_cross_project_by_default(tmp_path):
    ws_a = str(tmp_path / "a")
    ws_b = str(tmp_path / "b")
    ws_c = str(tmp_path / "c")            # unrelated current project
    plan_mod.save_plan(plan_mod.Plan(goal="部署 Docker 服务", workspace_dir=ws_a,
                                     steps=[plan_mod.PlanStep("写 Dockerfile")]))
    plan_mod.save_plan(plan_mod.Plan(goal="写周报", workspace_dir=ws_b,
                                     steps=[plan_mod.PlanStep("汇总")]))
    # searching from project c still recalls project a's episode
    hits = plan_mod.search_plans(ws_c, "docker 部署")
    assert hits and hits[0].goal == "部署 Docker 服务"


def test_search_plans_scope_project_only(tmp_path):
    ws_a = str(tmp_path / "a")
    ws_c = str(tmp_path / "c")
    plan_mod.save_plan(plan_mod.Plan(goal="部署 Docker 服务", workspace_dir=ws_a,
                                     steps=[plan_mod.PlanStep("写 Dockerfile")]))
    # project scope excludes other projects
    assert plan_mod.search_plans(ws_c, "docker 部署", cross_project=False) == []


# ── tools ──

def _emit(et, d):
    pass


def test_plan_update_stores_on_state_and_renders():
    from research_agent.tools.builtin.plan import _handle_plan_update
    st = AgentState(user_input="t")
    st.workspace_dir = _ws()
    res = _handle_plan_update(
        {"goal": "重构模块",
         "steps": [{"step": "拆函数", "status": "done"},
                   {"step": "加测试", "depends_on": [1]}]},
        None, st, _emit)
    assert res.success
    assert res.data["total"] == 2
    from research_agent.plan_state import current_plan
    assert current_plan(st).goal == "重构模块"
    assert current_plan(st).steps[1].depends_on == [1]
    assert "plan" not in st.caps
    assert "重构模块" in res.data["message"]


def test_plan_update_rejects_empty():
    from research_agent.tools.builtin.plan import _handle_plan_update
    st = AgentState(user_input="t")
    assert not _handle_plan_update({"steps": []}, None, st, _emit).success


def test_plan_search_tool_finds_saved(tmp_path):
    from research_agent.tools.builtin.plan import _handle_plan_search
    ws = str(tmp_path)
    plan_mod.save_plan(plan_mod.Plan(goal="整理会议记录", workspace_dir=ws,
                                     steps=[plan_mod.PlanStep("提取要点")]))
    st = AgentState(user_input="t")
    st.workspace_dir = ws
    res = _handle_plan_search({"query": "整理会议记录"}, None, st, _emit)
    assert res.success and res.data["found"] == 1
    assert "整理会议记录" in res.data["message"]
    # miss
    miss = _handle_plan_search({"query": "zzz-unknown-topic"}, None, st, _emit)
    assert miss.data["found"] == 0


# ── run-level persistence + automatic episode write ──

def test_maybe_save_episode_persists_explicit_plan(tmp_path):
    from research_agent import agent as ag
    ws = str(tmp_path)
    st = AgentState(user_input="t")
    st.workspace_dir = ws
    st.caps["plan"] = plan_mod.Plan(goal="跑实验",
                                    steps=[plan_mod.PlanStep("跑基线")]).to_dict()
    ag._maybe_save_episode(st, ws, "chat_x", tool_path=["shell_exec", "file_read"])
    plans = plan_mod.list_plans(ws)
    assert len(plans) == 1 and plans[0].goal == "跑实验"
    assert plans[0].chat_id == "chat_x"
    assert set(plans[0].tools_used) == {"shell_exec", "file_read"}


def test_auto_episode_from_tool_path_without_plan(tmp_path):
    from research_agent import agent as ag
    from research_agent import plan_pipeline
    ws = str(tmp_path)
    st = AgentState(user_input="帮我部署服务")
    st.workspace_dir = ws
    st.final_response = "部署完成"
    ag._maybe_save_episode(st, ws, "c", tool_path=["file_write", "shell_exec"])
    plan_pipeline.drain()                      # async write
    plans = plan_mod.list_plans(ws)
    assert len(plans) == 1
    ep = plans[0]
    assert ep.goal == "帮我部署服务"
    assert set(ep.tools_used) == {"file_write", "shell_exec"}
    assert ep.outcome == "部署完成"


def test_no_episode_without_plan_or_tools(tmp_path):
    from research_agent import agent as ag
    ws = str(tmp_path)
    st = AgentState(user_input="你好")
    ag._maybe_save_episode(st, ws, "c", tool_path=[])
    assert plan_mod.list_plans(ws) == []


# ── LLM episode distillation ──

def test_distill_episode_from_llm():
    import json
    from research_agent.llm import MockLLMProvider
    llm = MockLLMProvider([json.dumps({
        "goal": "部署服务",
        "decisions": [{"decision": "停机迁移", "reason": "数据量小"}],
        "steps": [{"step": "写 Dockerfile", "status": "done", "result": "OK"}],
        "outcome": "完成",
    }, ensure_ascii=False)])
    digest = {"user_input": "部署", "final_response": "完成",
              "tool_path": ["shell_exec"], "conversation": ""}
    p = plan_mod.distill_episode(llm, digest, workspace_dir="w", chat_id="c")
    assert p.goal == "部署服务"
    assert p.decisions == ["停机迁移 — 数据量小"]
    assert p.steps[0].step == "写 Dockerfile" and p.steps[0].status == "done"
    assert p.tools_used == ["shell_exec"]


def test_distill_episode_bad_or_empty_returns_none():
    from research_agent.llm import MockLLMProvider
    assert plan_mod.distill_episode(MockLLMProvider(["not json at all"]),
                                    {"tool_path": []}) is None
    assert plan_mod.distill_episode(MockLLMProvider(["{}"]), {"tool_path": []}) is None
    assert plan_mod.distill_episode(None, {}) is None


def test_maybe_save_episode_uses_llm_distill(tmp_path):
    import json
    from research_agent import agent as ag, plan_pipeline
    from research_agent.llm import MockLLMProvider
    ws = str(tmp_path)
    st = AgentState(user_input="u")
    st.workspace_dir = ws
    st.final_response = "r"
    llm = MockLLMProvider([json.dumps({"goal": "蒸馏目标", "decisions": ["d1"],
                                       "steps": [], "outcome": "o"}, ensure_ascii=False)])
    ag._maybe_save_episode(st, ws, "c", llm=llm, tool_path=["shell_exec"])
    plan_pipeline.drain()
    plans = plan_mod.list_plans(ws)
    assert plans and plans[0].goal == "蒸馏目标"
    assert plans[0].decisions == ["d1"]


def test_pipeline_distill_then_save(tmp_path):
    import json
    from research_agent import plan_pipeline
    from research_agent.llm import MockLLMProvider
    ws = str(tmp_path)
    llm = MockLLMProvider([json.dumps({"goal": "任务", "decisions": [],
                                       "steps": [], "outcome": "ok"}, ensure_ascii=False)])
    plan_pipeline.submit_run(user_input="u", final_response="ok", tool_path=["file_read"],
                             workspace_dir=ws, chat_id="c", llm=llm, use_llm=True)
    plan_pipeline.drain()
    plans = plan_mod.list_plans(ws)
    assert plans and plans[0].goal == "任务"


def test_search_matches_decisions_and_outcome(tmp_path):
    ws = str(tmp_path)
    plan_mod.save_plan(plan_mod.Plan(
        goal="迁移数据库", workspace_dir=ws,
        decisions=["选择停机迁移而非双写，因为数据量小"],
        outcome="迁移成功", steps=[plan_mod.PlanStep("备份")]))
    hits = plan_mod.search_plans(ws, "停机迁移 双写")
    assert hits and hits[0].goal == "迁移数据库"


def test_plan_decisions_reason_roundtrip():
    p = plan_mod.Plan(goal="g", steps=[plan_mod.PlanStep("s", reason="因为X")],
                      decisions=["决策A"], outcome="完成", tools_used=["shell_exec"])
    p2 = plan_mod.Plan.from_dict(p.to_dict())
    assert p2.steps[0].reason == "因为X"
    assert p2.decisions == ["决策A"]
    assert p2.outcome == "完成"
    assert p2.tools_used == ["shell_exec"]


# ── plugin registration ──

def test_plan_plugin_registered():
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    register_builtins()
    reg = get_registry()
    assert "plan" in reg.plugins
    assert "plan_update" in reg
    assert "plan_search" in reg
    assert reg.tools["plan_update"].plugin_id == "plan"
