"""Project auto-routing: match user input to existing projects or detect new ones."""
import jieba

from research_agent.models import Project


def _tokenize(text: str) -> set[str]:
    """Tokenize text. Uses jieba for Chinese, space-split for non-Chinese."""
    if any('\u4e00' <= c <= '\u9fff' for c in text):
        return set(jieba.cut(text))
    return set(text.lower().split())


def _compute_match_score(user_input: str, project: Project) -> float:
    """Simple keyword overlap + substring matching. No LLM needed."""
    input_lower = user_input.lower()
    topic_lower = project.topic.lower()

    score = 0.0
    topic_words = _tokenize(topic_lower)
    input_words = _tokenize(input_lower)

    # Exact word overlap
    overlap = topic_words & input_words
    if overlap:
        score += len(overlap) / max(len(topic_words), 1) * 0.6

    # Substring matching
    for word in topic_words:
        if len(word) > 3 and word in input_lower:
            score += 0.3

    # History summary keyword check
    if project.history_summary:
        hist_lower = project.history_summary.lower()
        for w in input_words:
            if len(w) > 3 and w in hist_lower:
                score += 0.1

    return min(score, 1.0)


def route_to_project(user_input: str, projects: list[Project]) -> Project | None:
    if not projects:
        return None

    best = None
    best_score = 0.0

    for proj in projects:
        score = _compute_match_score(user_input, proj)
        if score > best_score:
            best_score = score
            best = proj

    if best_score > 0.15:
        return best
    return None


def should_create_project(user_input: str, projects: list[Project]) -> bool:
    if not projects:
        return True
    match = route_to_project(user_input, projects)
    return match is None


def extract_project_topic(user_input: str) -> str:
    indicators = ["新项目", "开个项目", "新建项目", "create project", "new project"]
    topic = user_input
    for ind in indicators:
        idx = topic.lower().find(ind)
        if idx >= 0:
            topic = topic[idx + len(ind):]
            break
    topic = topic.strip("，,：:。. ")
    return topic