from research_agent.agent import _build_resume_message, _detect_pending_task
from research_agent.models import Project, ProjectStatus, PendingTask


def test_build_resume_message():
    p = Project(
        id="p1", topic="HPLC分析",
        status=ProjectStatus.WAITING,
        pending_task=PendingTask(description="跑HPLC实验", expected_time="2-3天"),
        history_summary="讨论了HPLC方法选择"
    )
    msg = _build_resume_message(p)
    assert "HPLC分析" in msg
    assert "跑HPLC实验" in msg
    assert "2-3天" in msg
    assert "HPLC方法选择" in msg


def test_detect_pending_task():
    task = _detect_pending_task("这个需要你手动跑一次HPLC实验，然后给我数据。")
    assert task is not None
    assert "HPLC" in task.description


def test_detect_no_pending_task():
    task = _detect_pending_task("根据文献，HPLC方法已经成熟。")
    assert task is None


def test_detect_pending_task_chinese():
    task = _detect_pending_task("需要你完成实验操作，我无法执行湿实验。")
    assert task is not None