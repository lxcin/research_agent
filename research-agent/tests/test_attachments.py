# tests/test_attachments.py — attachment staging/parsing (chat-scoped, offline).
import os

import pytest

from research_agent import attachments as A
from research_agent.context import _content_text, build_context, trim_messages
from research_agent.models import AgentState


def test_stage_rejects_unsupported_and_large(temp_data_dir):
    with pytest.raises(ValueError):
        A.stage("x.exe", b"binary", chat_id="c1")
    with pytest.raises(ValueError):
        A.stage("big.txt", b"x" * (21 * 1024 * 1024), chat_id="c1")


def test_stage_chat_scoped_layout_and_dedup(temp_data_dir):
    m1 = A.stage("a.py", b"print(1)", chat_id="c1")
    assert m1["kind"] == "text" and m1["ext"] == ".py"
    assert os.path.isfile(os.path.join(m1["dir"], "meta.json"))
    assert os.path.isfile(m1["original"])
    # chat-scoped path contains the chat id
    assert os.path.join("attachments", "c1") in m1["dir"].replace("/", os.sep)
    m2 = A.stage("a.py", b"print(1)", chat_id="c1")
    assert m2["id"] == m1["id"]           # dedup by sha256
    # different chat → separate
    m3 = A.stage("a.py", b"print(1)", chat_id="c2")
    assert m3["id"] != m1["id"]


def test_parse_text_and_truncate(temp_data_dir):
    m = A.stage("a.txt", b"x" * 100, chat_id="c")
    blocks = A.parse(m, config={"max_chars": 10})
    assert blocks == [{"type": "text", "text": "x" * 10}]


def test_parse_image_block(temp_data_dir):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    m = A.stage("pic.png", png, chat_id="c")
    blocks = A.parse(m)
    assert blocks[0]["type"] == "image_url"
    assert blocks[0]["image_url"]["url"].startswith("data:image/")


def test_parse_pdf_and_office_dispatch(temp_data_dir, monkeypatch):
    monkeypatch.setattr(A, "_parse_pdf", lambda data, cfg: "PDF TEXT")
    mp = A.stage("p.pdf", b"%PDF-1.7 x", chat_id="c")
    assert A.parse(mp)[0]["text"] == "PDF TEXT"

    monkeypatch.setattr(A, "_parse_office", lambda data, ext, cfg: "DOC TEXT")
    md = A.stage("d.docx", b"PK\x03\x04", chat_id="c")
    assert A.parse(md)[0]["text"] == "DOC TEXT"


def test_parse_file_end_to_end(tmp_path, temp_data_dir):
    p = tmp_path / "x.py"
    p.write_text("print(1)", encoding="utf-8")
    blocks = A.parse_file(str(p), chat_id="default")
    assert blocks[0]["type"] == "text" and "print(1)" in blocks[0]["text"]


def test_list_and_cleanup(temp_data_dir):
    A.stage("a.py", b"1", chat_id="c1")
    A.stage("b.txt", b"2", chat_id="c2")
    assert len(A.list_attachments("c1")) == 1
    assert A.cleanup("c1") == 1
    assert A.list_attachments("c1") == []
    assert len(A.list_attachments("c2")) == 1
    assert A.cleanup() >= 1
    assert A.list_attachments("c2") == []


# ── context integration (multimodal) ──

def test_content_text_flattens_blocks():
    content = [{"type": "text", "text": "hi"},
               {"type": "image_url", "image_url": {"url": "x"}}]
    assert _content_text(content) == "hi [image]"


def test_build_context_injects_attachment_blocks(tmp_path):
    s = AgentState(user_input="看这张图")
    s.workspace_dir = str(tmp_path)
    s.attachment_blocks = [{"type": "text", "text": "code"},
                           {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
    msgs = build_context(s, None, "")
    last = msgs[-1]
    assert last["role"] == "user" and isinstance(last["content"], list)
    assert last["content"][0]["type"] == "text"
    assert any(c.get("type") == "image_url" for c in last["content"])


def test_build_context_text_attachments_merged(tmp_path):
    s = AgentState(user_input="解释")
    s.workspace_dir = str(tmp_path)
    s.attachment_blocks = [{"type": "text", "text": "code A"},
                           {"type": "text", "text": "code B"}]
    last = build_context(s, None, "")[-1]
    assert isinstance(last["content"], str)          # text-only → merged string
    assert "解释" in last["content"]
    assert "code A" in last["content"] and "code B" in last["content"]


def test_generate_msgs_includes_attachment_blocks():
    from research_agent.runtime import _generate_msgs
    s = AgentState(user_input="看图")
    s.attachment_blocks = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
    msgs = _generate_msgs([], s)
    last = msgs[-1]
    assert last["role"] == "user" and isinstance(last["content"], list)
    assert last["content"][0]["text"] == "看图"
    assert any(c.get("type") == "image_url" for c in last["content"])


def test_trim_messages_tolerates_block_content():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "x" * 10}]}]
    assert trim_messages(msgs, 100000) == msgs   # block content not mangled


# ── CLI ──

def test_cli_attachments_list_cleanup(temp_data_dir):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    A.stage("a.py", b"print(1)", chat_id="default")
    runner = CliRunner()
    res = runner.invoke(cli_mod.attachments, ["list"])
    assert res.exit_code == 0 and "a.py" in res.output
    res = runner.invoke(cli_mod.attachments, ["cleanup"])
    assert res.exit_code == 0
    assert A.list_attachments("default") == []
