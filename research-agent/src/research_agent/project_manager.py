"""Workspace-based project management system.

Replaces SQLite-based project management with file-system-based storage.
"""

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from research_agent.config import get_data_dir

_FILE_LOCK = threading.Lock()
_WORKSPACE_DIR = ".research-agent"


def _get_projects_dir() -> Path:
    return get_data_dir() / "projects"


def _get_project_dir(project_id: str) -> Path:
    return _get_projects_dir() / project_id


def _get_chats_dir(project_id: str) -> Path:
    return _get_project_dir(project_id) / "conversations"


def get_project_id(workspace_dir: str) -> str:
    return hashlib.sha256(workspace_dir.encode()).hexdigest()[:16]


def is_project_dir(workspace_dir: str) -> bool:
    marker = Path(workspace_dir) / _WORKSPACE_DIR / "project.json"
    return marker.exists()


def init_project(workspace_dir: str, topic: str = "") -> dict:
    project_id = get_project_id(workspace_dir)
    now = datetime.now().isoformat()
    project = {
        "project_id": project_id,
        "topic": topic,
        "workspace_dir": workspace_dir,
        "created_at": now,
        "updated_at": now,
        "active_workspace_id": None,
        "status": "active",
    }

    project_dir = _get_project_dir(project_id)
    os.makedirs(project_dir, exist_ok=True)
    os.makedirs(_get_chats_dir(project_id), exist_ok=True)

    with _FILE_LOCK:
        _write_json(project_dir / "project.json", project)
        progress_path = project_dir / "progress.md"
        if not progress_path.exists():
            progress_path.write_text("", encoding="utf-8")

    write_workspace_marker(workspace_dir, project_id)
    return project


def load_project(workspace_dir: str) -> dict | None:
    marker = read_workspace_marker(workspace_dir)
    if not marker:
        return None
    project_id = marker.get("project_id")
    if not project_id:
        return None
    project_path = _get_project_dir(project_id) / "project.json"
    return _read_json(project_path)


def list_projects() -> list[dict]:
    projects_dir = _get_projects_dir()
    if not projects_dir.exists():
        return []
    result = []
    for child in projects_dir.iterdir():
        if child.is_dir():
            proj_file = child / "project.json"
            proj = _read_json(proj_file)
            if proj:
                result.append(proj)
    result.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return result


def list_chats(workspace_dir: str) -> list[dict]:
    project_id = get_project_id(workspace_dir)
    chats_dir = _get_chats_dir(project_id)
    if not chats_dir.exists():
        return []
    result = []
    for chat_file in sorted(chats_dir.glob("*.json")):
        chat = _read_json(chat_file)
        if chat:
            result.append({
                "chat_id": chat.get("chat_id", ""),
                "title": chat.get("title", ""),
                "created_at": chat.get("created_at", ""),
                "turn_count": len(chat.get("turns", [])),
                "workspace_dir": chat.get("workspace_dir", ""),
            })
    return result


def create_chat(workspace_dir: str, title: str = "") -> str:
    project_id = get_project_id(workspace_dir)
    chat_id = f"chat_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()
    chat = {
        "chat_id": chat_id,
        "title": title,
        "workspace_dir": workspace_dir,
        "created_at": now,
        "updated_at": now,
        "turns": [],
        "compressed_summary": "",
    }
    chats_dir = _get_chats_dir(project_id)
    os.makedirs(chats_dir, exist_ok=True)
    with _FILE_LOCK:
        _write_json(chats_dir / f"{chat_id}.json", chat)
    return chat_id


def load_chat(workspace_dir: str, chat_id: str) -> dict | None:
    project_id = get_project_id(workspace_dir)
    chat_path = _get_chats_dir(project_id) / f"{chat_id}.json"
    return _read_json(chat_path)


def delete_chat(workspace_dir: str, chat_id: str) -> bool:
    project_id = get_project_id(workspace_dir)
    chat_path = _get_chats_dir(project_id) / f"{chat_id}.json"
    if not chat_path.exists():
        return False
    with _FILE_LOCK:
        chat_path.unlink()
    return True


def update_chat(workspace_dir: str, chat_id: str, updates: dict) -> bool:
    project_id = get_project_id(workspace_dir)
    chat_path = _get_chats_dir(project_id) / f"{chat_id}.json"
    chat = _read_json(chat_path)
    if not chat:
        return False
    if "title" in updates:
        chat["title"] = updates["title"]
    if "workspace_dir" in updates:
        chat["workspace_dir"] = updates["workspace_dir"]
    chat["updated_at"] = datetime.now().isoformat()
    with _FILE_LOCK:
        _write_json(chat_path, chat)
    return True


def save_turn(workspace_dir: str, chat_id: str, round_number: int,
              user_message: str, assistant_message: str,
              sections: list[dict] | None = None) -> None:
    project_id = get_project_id(workspace_dir)
    chat_path = _get_chats_dir(project_id) / f"{chat_id}.json"
    chat = _read_json(chat_path)
    if not chat:
        return
    now = datetime.now().isoformat()
    turn = {
        "round": round_number,
        "user": user_message,
        "assistant": assistant_message,
        "sections": sections or [],
        "compressed": False,
        "summary": "",
        "timestamp": now,
    }
    chat["turns"].append(turn)
    chat["updated_at"] = now
    with _FILE_LOCK:
        _write_json(chat_path, chat)


def get_recent_turns(workspace_dir: str, chat_id: str, limit: int = 20) -> list[dict]:
    chat = load_chat(workspace_dir, chat_id)
    if not chat:
        return []
    turns = chat.get("turns", [])
    recent = turns[-limit:]
    return [
        {
            "round": t["round"],
            "user": t["user"],
            "assistant": t["assistant"],
            "compressed": t.get("compressed", False),
            "summary": t.get("summary", ""),
            "timestamp": t.get("timestamp", ""),
        }
        for t in recent
    ]


def count_uncompressed_turns(workspace_dir: str, chat_id: str) -> int:
    chat = load_chat(workspace_dir, chat_id)
    if not chat:
        return 0
    return sum(1 for t in chat.get("turns", []) if not t.get("compressed", False))


def mark_compressed(workspace_dir: str, chat_id: str,
                    turn_indices: list[int], summary: str) -> None:
    project_id = get_project_id(workspace_dir)
    chat_path = _get_chats_dir(project_id) / f"{chat_id}.json"
    chat = _read_json(chat_path)
    if not chat:
        return
    for idx in turn_indices:
        if 0 <= idx < len(chat["turns"]):
            chat["turns"][idx]["compressed"] = True
            chat["turns"][idx]["summary"] = summary
    chat["compressed_summary"] = summary
    chat["updated_at"] = datetime.now().isoformat()
    with _FILE_LOCK:
        _write_json(chat_path, chat)


def load_progress(workspace_dir: str) -> str:
    project_id = get_project_id(workspace_dir)
    progress_path = _get_project_dir(project_id) / "progress.md"
    if progress_path.exists():
        return progress_path.read_text(encoding="utf-8")
    return ""


def update_progress(workspace_dir: str, content: str) -> None:
    project_id = get_project_id(workspace_dir)
    progress_path = _get_project_dir(project_id) / "progress.md"
    with _FILE_LOCK:
        progress_path.write_text(content, encoding="utf-8")
    snapshot = "<!-- AUTO-GENERATED BY PaperPilot — DO NOT EDIT BY HAND -->\n\n" + content
    workspace_snapshot_path = Path(workspace_dir) / _WORKSPACE_DIR / "progress.md"
    with _FILE_LOCK:
        workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        workspace_snapshot_path.write_text(snapshot, encoding="utf-8")



def write_workspace_marker(workspace_dir: str, project_id: str,
                           active_workspace_id: str | None = None) -> None:
    marker_dir = Path(workspace_dir) / _WORKSPACE_DIR
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = {
        "project_id": project_id,
        "active_workspace_id": active_workspace_id,
        "workspace_dir": workspace_dir,
    }
    with _FILE_LOCK:
        _write_json(marker_dir / "project.json", marker)


def read_workspace_marker(workspace_dir: str) -> dict | None:
    marker_path = Path(workspace_dir) / _WORKSPACE_DIR / "project.json"
    return _read_json(marker_path)


def _read_json(path: Path) -> dict | None:
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _write_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
