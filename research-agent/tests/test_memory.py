from research_agent.memory import store_turn, get_recent_turns, init_conversation_table
from research_agent.store import init_db, insert_project
from research_agent.models import Project, ProjectStatus


def test_store_and_retrieve_turns(temp_data_dir):
    init_db()
    init_conversation_table()
    pid = insert_project(Project(id="p1", topic="Test", status=ProjectStatus.ACTIVE))
    store_turn(pid, 1, "hello", "hi there")
    turns = get_recent_turns(pid)
    assert len(turns) == 1
    assert turns[0].user_message == "hello"
    assert turns[0].assistant_message == "hi there"