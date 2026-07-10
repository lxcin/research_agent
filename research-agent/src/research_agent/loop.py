"""Agentic Loop: self-check, retrieval evaluation, boundary awareness."""
from research_agent.models import AgentState


def evaluate_retrieval_sufficiency(state: AgentState) -> bool:
    chunks = state.retrieved_chunks
    if not chunks:
        return False
    return True


def self_check(state: AgentState) -> AgentState:
    if not state.final_response:
        state.error = "No response generated"
        return state
    if not state.retrieved_chunks and state.retry_count < 3:
        state.error = "No retrieval results, should retry"
        return state
    if state.retry_count >= 3:
        state.error = ""
        return state
    return state


def boundary_check(task_description: str) -> dict:
    task_lower = task_description.lower()

    lab_keywords = ["跑实验", "做实验", "hplc", "凝胶", "western blot", "pcr", "合成",
                     "滴定", "离心", "电泳", "色谱", "nmr", "质谱", "跑个", "帮我测"]
    for kw in lab_keywords:
        if kw in task_lower:
            return {
                "can_do": False,
                "reason": "这需要湿实验操作",
                "suggestion": "这步需要你来完成。完成后请把数据按我指定的格式发给我，我来帮你分析。",
            }

    retrieval_keywords = ["论文", "文献", "综述", "attention", "transformer",
                          "方法", "机制", "是什么", "怎么用", "对比", "总结", "分析"]
    for kw in retrieval_keywords:
        if kw in task_lower:
            return {
                "can_do": True,
                "reason": "可以通过知识库检索和相关文献来回答",
                "suggestion": "",
            }

    uncertain_keywords = ["精确值", "具体数值", "多少度", "多少克", "多少mol", "多少k", "自由能", "键能", "活化能", "晶格能"]
    for kw in uncertain_keywords:
        if kw in task_lower:
            return {
                "can_do": False,
                "reason": "不确定，需要实验数据",
                "suggestion": "我不确定这个的具体值——建议你查阅相关实验数据或文献，把数据给我后我来帮你分析。",
            }

    return {
        "can_do": True,
        "reason": "",
        "suggestion": "",
    }