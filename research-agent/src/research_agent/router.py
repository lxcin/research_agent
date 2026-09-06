"""Project topic extraction: pull a concise topic phrase from user input."""


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