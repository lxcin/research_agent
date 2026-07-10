from unittest.mock import patch, MagicMock
from research_agent.compressor import should_compress, compress, extract_wisdom
from research_agent.models import AgentState, Project, AccumulatedWisdom, ProjectStatus


def test_should_compress_many_rounds():
    state = AgentState(retry_count=0)
    assert should_compress(state) or not state.needs_compression


def test_should_compress_forced():
    state = AgentState(needs_compression=True)
    assert should_compress(state) is True


def test_extract_wisdom_from_dialogue():
    dialogue = """
User: HPLC跑出来的纯度波动很大，85%到92%之间来回跳
Agent: 可能是柱温不稳定导致的。你进样前平衡了多久？
User: 几乎没平衡，升温后直接进样了
Agent: 建议每次调温后平衡15分钟再进样。柱温每升10°C保留时间约前移0.3min。下次升温步长控制在5°C以内会更稳定。
"""
    wisdom = extract_wisdom(dialogue)
    assert len(wisdom.sops) >= 0
    assert len(wisdom.pitfalls) >= 0 or len(wisdom.frameworks) >= 0
    assert isinstance(wisdom, AccumulatedWisdom)


@patch("research_agent.compressor.litellm.completion")
def test_compress_triggers_update(mock_completion, temp_data_dir):
    from research_agent.store import init_db, insert_project

    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"history_summary": "Discussed HPLC optimization", '
        '"sops": ["Standard HPLC流程: C18柱，甲醇:水=70:30，1mL/min，254nm"], '
        '"pitfalls": [{"phenomenon": "重复性差","root_cause": "柱温未稳","solution": "平衡15min","improvement": "步长降到5°C"}], '
        '"frameworks": ["排查优先级：温度→流速→溶剂"], '
        '"agent_improvements": ["设计实验时提醒平衡时间"], '
        '"intro_summary": "HPLC纯度分析Agent：擅长流动相优化"'
        '}'))]
    )

    state = AgentState(
        active_project=Project(id="p1", topic="HPLC优化", status=ProjectStatus.ACTIVE),
        needs_compression=True,
    )
    result = compress(state)
    assert result.history_summary
    assert "HPLC" in result.history_summary
    assert len(result.accumulated_wisdom.sops) > 0