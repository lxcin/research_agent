"""ToolRouter — intent-to-tool-subset routing to reduce LLM tool confusion."""
import logging
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)

# Intent → specific tool names to expose
INTENT_ROUTES: dict[str, list[str]] = {
    "retrieve":     ["retrieve", "search_papers", "read_paper", "update_notes"],
    "read":         ["read_paper", "file_read", "retrieve", "update_notes"],
    "search":       ["search_papers", "retrieve", "read_paper"],
    "write":        ["file_write", "file_edit", "file_read", "file_glob", "git_checkpoint", "git_status"],
    "execute":      ["shell_exec", "check_tasks", "file_read", "file_write"],
    "git":          ["git_init", "git_checkpoint", "git_log", "git_rollback", "git_status"],
    "review":       ["retrieve", "search_papers", "read_paper", "update_notes",
                     "file_write", "file_edit", "file_read", "git_checkpoint"],
    "manage":       ["shell_exec", "check_tasks", "file_write", "file_edit",
                     "git_checkpoint", "git_log", "git_status"],
    "default":      [],  # all tools via fallback
}

# Keywords in user input that trigger each intent
INTENT_KEYWORDS: dict[str, list[str]] = {
    "retrieve": ["检索", "retrieve", "查找", "找", "有什么", "有没有", "哪些", "find paper", "find articles"],
    "read":     ["读", "read", "看内容", "内容是什么", "讲什么", "讲了什么", "说了什么"],
    "search":   ["搜索 arxiv", "search arxiv", "arxiv", "搜论文", "search for paper"],
    "write":    ["写", "write", "保存", "save", "创建文件", "create file", "记录", "做笔记"],
    "execute":  ["运行", "run", "执行", "exec", "shell", "实验", "experiment", "训练", "train"],
    "git":      ["git", "版本", "checkpoint", "回滚", "rollback", "检查点", "提交 commit"],
    "review":   ["综述", "review", "survey", "总结", "概括", "比较", "对比", "系统", "全面", "系统性地", "comprehensive"],
    "manage":   ["项目", "project", "管理", "设置", "配置", "switch", "切换"],
}


def categorize_intent(user_input: str) -> str:
    """Classify user input into an intent label. Returns 'default' if unclear."""
    lower = user_input.lower()
    scores: dict[str, int] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score:
            scores[intent] = score

    if not scores:
        return "default"

    # "review" gets a bonus (+2) since it implies the most comprehensive workflow
    if "review" in scores:
        scores["review"] += 2

    best = max(scores, key=lambda k: scores[k])
    return best


def route_tools(intent: str, registry) -> list[dict]:
    """Return filtered tool list for LLM based on intent."""
    tool_names = INTENT_ROUTES.get(intent)
    tools = registry.tools

    # Default: all tools
    if not tool_names:
        return registry.list_for_llm()

    filtered = {}
    for name in tool_names:
        if name in tools:
            filtered[name] = tools[name]

    # Always include spawn_subagent for complex intents
    if intent in ("review", "manage", "execute"):
        from research_agent.tools.subagent import spawn_subagent_tool
        filtered[spawn_subagent_tool.name] = spawn_subagent_tool

    return [t.to_openai_schema() for t in filtered.values()]


def add_anti_confusion_hints(descriptions: dict[str, str]) -> dict[str, str]:
    """Add 'NOT for' hints to tool descriptions to disambiguate similar tools."""
    # Pairs that LLMs often confuse: {name: "not_for_description"}
    confusion_map = {
        "retrieve": (
            "检索本地知识库中的论文片段和笔记。"
            "当本地没有结果时，应改用 search_papers 搜索 arXiv。"
        ),
        "search_papers": (
            "从 arXiv 搜索新论文并自动导入本地知识库。"
            "搜索前应先用 retrieve 检查本地是否已有。"
        ),
        "read_paper": (
            "读取本地知识库中已存储论文的完整内容。"
            "如果需要读的文件是用户上传的或新写的，用 file_read 代替。"
        ),
        "file_read": (
            "读取项目工作区中的原始文件内容（代码、笔记、数据文件）。"
            "读论文论文内容请用 read_paper，读工作区文件请用 file_read。"
        ),
        "file_write": (
            "创建新文件或完全覆盖已有文件。"
            "如果只是修改文件的部分内容，用 file_edit 代替。"
        ),
        "file_edit": (
            "精确替换文件中的部分内容。"
            "如果是创建新文件，用 file_write。需要先 file_read 确定要替换的文本。"
        ),
        "file_glob": (
            "按文件名模式查找文件（支持 * 和 ** 通配符）。"
            "如果要在文件内容中搜索关键词，用 file_grep 代替。"
        ),
        "file_grep": (
            "在文件内容中使用正则表达式搜索匹配行。"
            "如果只是找文件名，用 file_glob。"
        ),
        "shell_exec": (
            "运行 Shell 命令（可后台执行）。"
            "后台任务完成后用 check_tasks 查看结果。"
        ),
        "check_tasks": (
            "检查后台 shell_exec 任务的运行状态和输出。"
            "只在 shell_exec 设置了 background=true 后使用。"
        ),
        "git_checkpoint": (
            "保存当前工作区的 git 检查点（commit）。"
            "查看历史请用 git_log，恢复请用 git_rollback。"
        ),
        "git_rollback": (
            "将工作区恢复到之前的检查点（git reset --hard，会覆盖未保存的更改）。"
            "先用 git_log 查看历史，确认要恢复到的 commit hash。"
        ),
        "update_notes": (
            "更新项目笔记或论文阅读笔记到知识库。"
            "保存原始文件到工作区请用 file_write。"
        ),
    }

    result = {}
    for name, desc in descriptions.items():
        extra = confusion_map.get(name, "")
        if extra:
            result[name] = f"{desc}\n[使用说明] {extra}"
        else:
            result[name] = desc

    return result


def compute_similarity_scores(descriptions: dict[str, str]) -> list[dict]:
    """Compute pairwise similarity of tool descriptions. Flags >70% as potential confusion."""
    pairs = []
    names = list(descriptions.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            score = SequenceMatcher(None, descriptions[names[i]].lower(),
                                   descriptions[names[j]].lower()).ratio()
            if score > 0.5:
                pairs.append({
                    "tool_a": names[i],
                    "tool_b": names[j],
                    "similarity": round(score, 3),
                    "warning": score > 0.70,
                })
    pairs.sort(key=lambda x: x["similarity"], reverse=True)
    return pairs


def audit_tool_confusion(registry) -> dict:
    """Run a full confusion audit on registered tools. Returns {pairs, warnings}."""
    tools = registry.tools
    descs = {name: tool.description for name, tool in tools.items()}
    enhanced = add_anti_confusion_hints(descs)

    scores = compute_similarity_scores(enhanced)
    warnings = [p for p in scores if p["warning"]]

    return {
        "total_tools": len(tools),
        "total_pairs": len(scores),
        "warnings": len(warnings),
        "critical_pairs": warnings,
        "all_pairs": scores[:15],
        "intent_routes": {k: len(v) for k, v in INTENT_ROUTES.items()},
    }


def build_routing_report(intent: str, registry) -> str:
    """Generate a system prompt note about the current routing decision."""
    tools_count = len(registry.tools)
    filtered = route_tools(intent, registry)
    tool_names = INTENT_ROUTES.get(intent, INTENT_ROUTES["default"])
    return (
        f"[工具路由] 用户意图: {intent} | "
        f"可用工具: {len(filtered)}/{tools_count} "
        f"(工具: {', '.join(tool_names[:5])}{'...' if len(tool_names) > 5 else ''})"
    )
