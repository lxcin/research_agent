"""Runtime brief — a compact, dynamically derived "operating context" injected as
one system message before the agent starts.

Three sections:
  [运行环境] where it works — OS/shell/cwd/interpreter/pm/sandbox/net (FACTS,
             derived at runtime from the host; NOT hardcoded platform rules, so it
             is correct across OSes).
  [工作对象] who/what it works on — workspace + project topic.
  [工作契约] what "done" looks like — goal, done criteria, style, safety.

Kept small (a few hundred tokens) and placed in the stable prefix for prompt-cache
friendliness.
"""
from __future__ import annotations

import os
import platform
import shutil

_SANDBOX_HINT = {"local": "本机直跑(无隔离)", "docker": "一次性容器(隔离)"}


def _interpreter() -> str:
    """Best interpreter invocation for THIS host (Windows often needs `py`)."""
    if os.name == "nt":
        return "py -3" if shutil.which("py") else "python"
    return "python3" if shutil.which("python3") else "python"


def _environment_lines(workspace_dir: str) -> list[str]:
    lines = [f"os={platform.system()} {platform.release()}",
             "shell=" + ("cmd.exe" if os.name == "nt" else "/bin/sh"),
             f"cwd={workspace_dir}"]
    py = _interpreter()
    lines += [f"python=`{py}`", f"装包=`{py} -m pip`"]
    try:
        from research_agent.sandbox import get_sandbox_config, resolve_backend
        backend, _warn = resolve_backend(get_sandbox_config())
        lines.append(f"sandbox={backend}({_SANDBOX_HINT.get(backend, '')})")
    except Exception:
        pass
    try:
        from research_agent.tools import is_plugin_enabled
        lines.append("出站网络=" + ("on" if is_plugin_enabled("web") else "off(web插件未开)"))
    except Exception:
        pass
    return lines


def _principal_lines(state, workspace_dir: str) -> list[str]:
    lines = [f"workspace={workspace_dir}"]
    proj = getattr(state, "active_project", None)
    topic = getattr(proj, "topic", "") if proj is not None else ""
    if topic:
        lines.append(f"项目={topic}")
    return lines


def _contract_lines(state) -> list[str]:
    ui = (getattr(state, "user_input", "") or "").strip().splitlines()
    lines = []
    if ui and ui[0].strip():
        lines.append(f"本次目标={ui[0].strip()[:120]}")
    lines += ["完成标准=可运行/测试通过/产物落地(按任务)",
              "自检=产出或改动代码后要运行测试/编译确认；失败先修再交付，不把失败当完成",
              "风格=简短给结果、不道歉、不问不必要的确认",
              "安全=危险命令需审批；文件改动以 diff 提案呈现"]
    return lines


def build_runtime_brief(state, workspace_dir: str = "") -> str:
    """Compose the operating brief from live host facts + state. Never raises."""
    try:
        ws = workspace_dir or getattr(state, "workspace_dir", "") or "."
        sections = [("运行环境", _environment_lines(ws)),
                    ("工作对象", _principal_lines(state, ws)),
                    ("工作契约", _contract_lines(state))]
        parts = [f"[{name}] " + "；".join(vals) for name, vals in sections if vals]
        return "运行简报（开工前先了解）：\n" + "\n".join(parts)
    except Exception:
        return ""
