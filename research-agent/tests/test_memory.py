from research_agent.memory import store_turn, get_recent_turns


def test_store_and_retrieve_turns(temp_data_dir, temp_workspace):
    workspace_dir, chat_id = temp_workspace
    store_turn(workspace_dir, chat_id, 1, "hello", "hi there")
    turns = get_recent_turns(workspace_dir, chat_id)
    assert len(turns) == 1
    assert turns[0].user_message == "hello"
    assert turns[0].assistant_message == "hi there"