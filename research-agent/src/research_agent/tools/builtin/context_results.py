from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent.context_budget import artifact_directory


def read_result(params, llm, state, emit):
    import re
    key = params.get("artifact_id", "")
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        return ToolResult.fail("Invalid artifact id")
    offset = max(0, int(params.get("offset", 0)))
    limit = min(6000, max(1, int(params.get("limit", 3000))))
    path = artifact_directory(state) / (key + ".json")
    if not path.is_file():
        return ToolResult.fail("Artifact not found in this conversation")
    text = path.read_text(encoding="utf-8")
    return ToolResult.ok(text=text[offset:offset + limit], next_offset=offset + limit,
                         has_more=offset + limit < len(text), evidence="recorded_tool_payload",
                         note="这是工具调用当时的记录，不代表当前资源状态。")


context_result_tool = ToolSchema("context_result_read", "分段读取当前会话中外置的完整工具结果；预览不足时调用，offset/limit 按字符计。",
    {"type": "object", "properties": {"artifact_id": {"type": "string"}, "offset": {"type": "integer"},
     "limit": {"type": "integer"}}, "required": ["artifact_id"]}, read_result, side_effect=False,
    usage="artifact_id 可取结果中的 _tool_result_id / artifact_id，或历史调用参数中的 _stored_call。引用用于回溯历史证据，不能替代读取当前实现。")
