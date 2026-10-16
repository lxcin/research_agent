"""PaperPilot end-to-end demo.

Runs a couple of real tasks with your configured LLM, then prints the runtime
telemetry, the framework-effectiveness audit, and a cross-project episodic
recall — i.e. the full evaluation story in one go.

Usage (cmd):
    cd /d E:\\PROJ\\research-agent
    py -3.12 demo.py            :: reuse the demo data/workspace under %TEMP%
    py -3.12 demo.py --fresh    :: wipe and start clean

It runs in an ISOLATED data dir (%TEMP%\\paperpilot-demo-data) by copying your
config.yml, so your real data is not touched.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.gettempdir())
DEMO_DATA = TMP / "paperpilot-demo-data"
DEMO_WS = TMP / "paperpilot-demo-ws"
DEMO_WS2 = TMP / "paperpilot-demo-ws2"


def banner(title: str):
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


def setup_isolated_data(fresh: bool):
    real = Path.home() / "research-agent-data"
    cfg = real / "config.yml"
    if not cfg.exists():
        print(f"[!] 找不到 {cfg}；请先配置 model/api_key（或设置 DEEPSEEK_API_KEY）。")
        sys.exit(1)
    if fresh and DEMO_DATA.exists():
        shutil.rmtree(DEMO_DATA, ignore_errors=True)
    DEMO_DATA.mkdir(parents=True, exist_ok=True)
    shutil.copy(cfg, DEMO_DATA / "config.yml")
    os.environ["RESEARCH_AGENT_DATA_DIR"] = str(DEMO_DATA)
    os.environ.setdefault("RESEARCH_AGENT_EMBEDDING", "0")  # 关键词召回（无向量层也能跑）


def run_task(llm, workspace: str, chat_id: str, prompt: str):
    from research_agent.agent import AgentState, run_agent
    banner(f"任务: {prompt}")

    def on_event(et, d):
        if et == "tool":
            tool = d.get("tool") or d.get("name") or ""
            status = d.get("status", "")
            if tool:
                print(f"   · 工具 {tool} {status}")
        elif et == "proposal":
            print(f"   · 改动 {d.get('path')} ({d.get('status')})")
        elif et == "confirm_required":
            print(f"   · 需审批 {d.get('tool')}")

    st = AgentState(workspace_dir=workspace, active_chat_id=chat_id, user_input=prompt)
    r = run_agent(prompt, llm, st, on_event=on_event,
                  workspace_dir=workspace, chat_id=chat_id)
    print(f"\n   答: {r.final_response or '(无回复)'}")
    return r


def show_telemetry():
    from research_agent import telemetry
    banner("运行期遥测 (telemetry)：token / 成本 / 时延 / 工具路径 / 阶段")
    rep = telemetry.report(limit=50)
    print(json.dumps({"runs": rep.get("runs"), **rep.get("totals", {}),
                      "avg": rep.get("avg"), "p95": rep.get("p95")},
                     ensure_ascii=False, indent=2))
    print("\n--- 报告节选 ---")
    print(telemetry.render_markdown(rep)[:1400])
    return rep


def show_audit(tel):
    from research_agent.diagnostics import audit as au
    banner("框架有效性审计 (audit)：工具健康 / 收敛 / 完成 / 效率 / 稳定 × 经济性")
    res = au.audit(limit=20, telemetry_rep=tel)
    print(au.render_markdown(res)[:2600])


def show_episodic_recall(llm):
    from research_agent import project_manager as pm, plan, plan_pipeline
    from research_agent.agent import AgentState, run_agent
    banner("跨项目情景记忆 (episodic)：换一个项目，问它以前怎么做，看它主动召回")

    plan_pipeline.drain()  # 等异步情景写入完成
    chat2 = pm.create_chat(str(DEMO_WS2), title="demo2")
    prompt = ("换了个新项目，我想给新代码写测试。先查一下我以前在别的项目里"
              "是怎么用 pytest 写并运行测试的，再给我步骤。")
    st = AgentState(workspace_dir=str(DEMO_WS2), active_chat_id=chat2, user_input=prompt)
    r = run_agent(prompt, llm, st, workspace_dir=str(DEMO_WS2), chat_id=chat2)
    print(f"   答: {r.final_response or '(无回复)'}\n")

    hits = plan.search_plans(str(DEMO_WS2), "pytest 写测试 运行", cross_project=True, limit=5)
    print(f"   [直接验证] 跨项目召回 {len(hits)} 条情景：")
    for h in hits:
        print(f"     - {h.goal}  (项目: {Path(h.workspace_dir).name})")


def main():
    ap = argparse.ArgumentParser(description="PaperPilot end-to-end demo")
    ap.add_argument("--fresh", action="store_true", help="清空演示数据后重来")
    ap.add_argument("--no-recall", action="store_true", help="跳过情景召回演示")
    args = ap.parse_args()

    setup_isolated_data(args.fresh)

    from research_agent import project_manager as pm
    from research_agent.llm import LiteLLMProvider

    for ws in (DEMO_WS, DEMO_WS2):
        ws.mkdir(parents=True, exist_ok=True)
        if not pm.is_project_dir(str(ws)):
            pm.init_project(str(ws), topic=ws.name)

    llm = LiteLLMProvider()
    print(f"模型: {llm.model}")
    print(f"演示数据目录: {DEMO_DATA}")
    print(f"演示工作区:   {DEMO_WS}")

    chat = pm.create_chat(str(DEMO_WS), title="demo")

    # ── 任务 1：写文件 + 运行（含写后自动验证链路） ──
    run_task(llm, str(DEMO_WS), chat,
             "在工作区创建 greet.py，内容为 print('hello world')；"
             "然后用 shell 运行它确认输出 hello world。")

    # ── 任务 2：多步 + 测试（触发 plan / pytest 自动验证） ──
    run_task(llm, str(DEMO_WS), chat,
             "在工作区创建 utils.py，定义 add(a, b) 返回两数之和；"
             "再创建 test_utils.py 用 pytest 测试 add；然后运行 pytest 确认通过。")

    # 等异步情景蒸馏写入
    try:
        from research_agent import plan_pipeline
        plan_pipeline.drain()
    except Exception:
        pass

    tel = show_telemetry()
    show_audit(tel)

    if not args.no_recall:
        show_episodic_recall(llm)

    banner("完成")
    print(f"产物文件: {(DEMO_WS / 'greet.py')}")
    print("可继续探索：")
    print("  py -3.12 -m research_agent.cli audit --report")
    print("  py -3.12 -m research_agent.cli quality --open")


if __name__ == "__main__":
    main()
