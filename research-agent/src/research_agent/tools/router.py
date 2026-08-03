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
    "write":        ["file_write", "file_edit", "file_read", "file_glob"],
    "execute":      ["shell_exec", "check_tasks", "file_read", "file_write"],
    "git":          ["shell_exec"],   # LLM uses shell_exec for git commands
    "review":       ["retrieve", "search_papers", "read_paper", "update_notes",
                     "file_write", "file_edit", "file_read"],
    "manage":       ["shell_exec", "check_tasks", "file_write", "file_edit"],
    "default":      ["retrieve", "search_papers", "read_paper"],  # no filesystem/shell
}

# Keywords in user input that trigger each intent
INTENT_KEYWORDS: dict[str, list[str]] = {
    "retrieve": ["检索", "retrieve", "查找", "找", "有什么", "有没有", "哪些", "find paper", "find articles",
                 "文件在哪", "文件位置", "在哪里", "搜一下", "查一下"],
    "read":     ["读", "read", "看内容", "内容是什么", "讲什么", "讲了什么", "说了什么"],
    "search":   ["搜索 arxiv", "search arxiv", "arxiv", "搜论文", "search for paper"],
    "write":    ["写", "write", "保存", "save", "创建文件", "create file", "记录", "做笔记", "帮我写"],
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
            "在本地知识库中搜索已存储的论文。仅搜本地。"
            "注意：刚用 search_papers 在 arXiv 找到的论文不在本地，不能用 retrieve 找它——直接用 read_paper(paper_id) 读。"
        ),
        "search_papers": (
            "在 arXiv 搜索论文，返回摘要列表。不会自动储存。"
            "找到论文后，直接用 read_paper(paper_id='...') 读取全文。不要用 retrieve。"
        ),
        "read_paper": (
            "读论文全文。传入 search_papers 返回的 paper_id，或本地 retrieve 到的 paper_id 都可以。"
            "首次读取时自动摄入知识库，后续可直接检索。"
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
            "git 操作（log/status/reset）也用 shell_exec 直接执行。"
        ),
        "check_tasks": (
            "检查后台 shell_exec 任务的运行状态和输出。"
            "只在 shell_exec 设置了 background=true 后使用。"
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
