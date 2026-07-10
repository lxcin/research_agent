# tests/test_store.py
from research_agent.store import init_db, insert_paper, get_paper, get_all_papers, delete_paper
from research_agent.models import Paper


def test_insert_and_get_paper(temp_data_dir):
    init_db()
    paper = Paper(title="Test Paper", doi="10.0000/test", year=2024, source_score=8,
                  authors=["Alice"], abstract="An abstract.")
    paper_id = insert_paper(paper)
    assert paper_id is not None

    fetched = get_paper(paper_id)
    assert fetched.title == "Test Paper"
    assert fetched.doi == "10.0000/test"
    assert fetched.source_score == 8
    assert len(fetched.authors) == 1
    assert fetched.authors[0] == "Alice"


def test_get_all_papers(temp_data_dir):
    init_db()
    insert_paper(Paper(title="P1", doi="doi1"))
    insert_paper(Paper(title="P2", doi="doi2"))
    papers = get_all_papers()
    assert len(papers) >= 2


def test_delete_paper(temp_data_dir):
    init_db()
    pid = insert_paper(Paper(title="ToDelete", doi="doi_del"))
    delete_paper(pid)
    assert get_paper(pid) is None

from research_agent.models import Project, ProjectStatus, PendingTask, PlanStep
from research_agent.store import insert_project, get_project, get_all_projects, update_project, delete_project


def test_insert_and_get_project(temp_data_dir):
    init_db()
    proj = Project(topic="Test Project", status=ProjectStatus.ACTIVE,
                   pending_task=PendingTask(description="Run HPLC",
                                            expected_format="CSV with retention_time and peak_area",
                                            expected_time="2 days"))
    pid = insert_project(proj)
    assert pid is not None

    fetched = get_project(pid)
    assert fetched.topic == "Test Project"
    assert fetched.status == ProjectStatus.ACTIVE
    assert fetched.pending_task.description == "Run HPLC"
    assert fetched.pending_task.expected_format == "CSV with retention_time and peak_area"


def test_get_all_projects(temp_data_dir):
    init_db()
    insert_project(Project(topic="P1"))
    insert_project(Project(topic="P2"))
    assert len(get_all_projects()) >= 2


def test_update_project(temp_data_dir):
    init_db()
    pid = insert_project(Project(topic="Original"))
    p = get_project(pid)
    p.topic = "Updated"
    p.history_summary = "Changed summary"
    update_project(p)
    fetched = get_project(pid)
    assert fetched.topic == "Updated"
    assert fetched.history_summary == "Changed summary"


def test_delete_project(temp_data_dir):
    init_db()
    pid = insert_project(Project(topic="ToDelete"))
    delete_project(pid)
    assert get_project(pid) is None