"""FastAPI API server for PaperPilot (API only — no bundled frontend)."""
import json
import uuid
import asyncio
import os
import threading
import queue

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from research_agent.agent import AgentState, run_agent

_active_states: dict[str, AgentState] = {}

app = FastAPI(title="PaperPilot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"name": "PaperPilot API", "docs": "/docs", "health": "/api/health"}


def _feature_guard(plugin_id: str, label: str = ""):
    """Reject a request when an optional plugin is disabled/uninstalled.

    Turns a would-be ImportError/500 (after plugin uninstall) into a clean 404
    with an explanation, so capability-backed API endpoints degrade gracefully.
    """
    from research_agent.tools import is_plugin_enabled
    if is_plugin_enabled(plugin_id):
        return
    name = label or plugin_id
    raise HTTPException(404, f"功能未启用或已卸载: {name}")


class ApiConfig(BaseModel):
    provider: str = ""
    apiKey: str = ""
    baseUrl: str = ""
    model: str = ""


class ChatRequest(BaseModel):
    message: str
    workspace_dir: str = ""
    chat_id: str = ""
    config: ApiConfig | None = None


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    user_config = req.config

    if not user_config or not user_config.apiKey:
        from research_agent.config import get_api_key as config_api_key
        if not config_api_key():
            async def gen():
                yield f"data: {json.dumps({'type': 'error', 'text': 'Please configure API Key'})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        # Config has key — proceed

    async def gen():
        yield f"data: {json.dumps({'type': 'start', 'id': str(uuid.uuid4())})}\n\n"

        q = queue.Queue()

        def emit(et: str, d: dict):
            q.put({"type": et, **d})

        def _run():
            try:
                # API key fallback: frontend > env var > config.yml
                from research_agent.config import get_api_key
                frontend_key = user_config.apiKey if user_config else ""
                api_key = frontend_key or get_api_key()
                if not api_key:
                    emit("error", {"text": "请先配置 API Key（设置面板 → 填入 Key，或设置环境变量）"})
                    emit("done", {})
                    return

                model = (user_config.model if user_config else "") or "deepseek/deepseek-chat"
                api_base = (user_config.baseUrl if user_config else "") or None

                from research_agent.llm import LiteLLMProvider
                llm = LiteLLMProvider(model=model, api_key=api_key, api_base=api_base)
                state = AgentState(user_input=req.message)
                workspace = req.workspace_dir or ""
                chat = req.chat_id or ""
                _active_states[chat or "default"] = state
                result = run_agent(req.message, llm, state, on_event=emit,
                                   workspace_dir=workspace, chat_id=chat)

                # Chunks already streamed via _stream_response - don't re-emit
                emit("done", {})
            except Exception as e:
                emit("error", {"text": f"处理失败: {str(e)}"})
                emit("done", {})

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        loop = asyncio.get_running_loop()
        while True:
            try:
                ev = await loop.run_in_executor(None, lambda: q.get(timeout=180))
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "done":
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'error', 'text': '请求超时'})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/workspaces")
async def list_workspaces():
    from research_agent import project_manager as pm
    projects = pm.list_projects()
    return [{
        "id": p["project_id"], "name": p.get("topic", "") or p["workspace_dir"],
        "workspace_dir": p["workspace_dir"], "status": p.get("status", "active"),
        "updated": p.get("updated_at", ""), "created": p.get("created_at", ""),
        "summary": "", "progress": 0, "steps": [],
    } for p in projects]


@app.get("/api/workspaces/info")
async def get_workspace_info(dir: str = ""):
    from research_agent import project_manager as pm
    project_id = pm.get_project_id(dir)
    projects = pm.list_projects()
    for p in projects:
        if p["project_id"] == project_id:
            return {"id": p["project_id"], "name": p.get("topic", ""),
                    "workspace_dir": p["workspace_dir"], "status": p.get("status", "active")}
    raise HTTPException(404, "Project not found")


@app.get("/api/workspaces/file")
async def serve_workspace_file(dir: str = "", path: str = ""):
    """Serve static files from workspace directory (HTML, images, etc.)."""
    proj_dir = dir
    file_path = os.path.join(proj_dir, path)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "File not found")
    resolved = os.path.normpath(os.path.abspath(file_path))
    if not resolved.startswith(os.path.normpath(os.path.abspath(proj_dir))):
        raise HTTPException(403)
    return FileResponse(file_path)


@app.get("/api/workspaces/files")
async def list_workspace_files(dir: str = ""):
    import os as _os

    proj_dir = dir
    if not proj_dir or not _os.path.isdir(proj_dir):
        return {"project_id": "", "dir": proj_dir, "files": []}

    files = []
    for root, dirs, filenames in _os.walk(proj_dir):
        depth = root.replace(str(proj_dir), "").count(_os.sep)
        if depth > 3:
            continue
        for name in filenames:
            full = _os.path.join(root, name)
            rel = _os.path.relpath(full, proj_dir).replace("\\", "/")
            try:
                size = _os.path.getsize(full)
            except Exception:
                size = 0
            files.append({"name": rel, "size": size})

    files.sort(key=lambda f: f["name"])
    return {"project_id": dir, "dir": str(proj_dir), "files": files[:100], "count": len(files)}


@app.get("/api/skills")
async def list_skills():
    from research_agent.skill_loader import load_skills_from_dir
    import os
    skills_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")
    if not os.path.isdir(skills_dir):
        skills_dir = os.path.join(os.getcwd(), "skills")
    loaded = load_skills_from_dir(skills_dir)
    return [{"name": s.name, "description": s.description, "triggers": s.triggers, "enabled": s.enabled, "file_path": s.file_path} for s in loaded]


@app.put("/api/skills/{name}")
async def save_skill(name: str, body: dict):
    import os
    skills_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")
    file_path = os.path.join(skills_dir, f"{name}.md")
    content = f"---\nname: {name}\ndescription: {body.get('description', '')}\ntriggers: {body.get('triggers', [])}\nenabled: {body.get('enabled', True)}\n---\n\n{body.get('body', '')}"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"status": "ok"}


@app.get("/api/chats")
async def list_chats(workspace: str = ""):
    from research_agent import project_manager as pm
    if workspace:
        return pm.list_chats(workspace)
    # scan all projects for chats
    projects = pm.list_projects()
    all_chats = []
    for p in projects:
        ws = p.get("workspace_dir", "")
        if ws:
            chats = pm.list_chats(ws)
            all_chats.extend(chats)
    return all_chats


@app.post("/api/chats")
async def create_chat(workspace: str = "", title: str = ""):
    if not workspace:
        from research_agent.config import get_data_dir
        workspace = str(get_data_dir() / "workspaces" / "default")
    from research_agent import project_manager as pm
    chat_id = pm.create_chat(workspace, title)
    return {"chat_id": chat_id}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str, workspace: str = ""):
    from research_agent import project_manager as pm
    if workspace:
        chat = pm.load_chat(workspace, chat_id)
        if chat:
            return chat
        raise HTTPException(404, "Chat not found")
    # search all projects for this chat
    projects = pm.list_projects()
    for p in projects:
        ws = p.get("workspace_dir", "")
        if ws:
            chat = pm.load_chat(ws, chat_id)
            if chat:
                return chat
    raise HTTPException(404, "Chat not found")


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, workspace: str = ""):
    from research_agent import project_manager as pm
    if workspace:
        if not pm.delete_chat(workspace, chat_id):
            raise HTTPException(404, "Chat not found")
        return {"status": "deleted"}
    projects = pm.list_projects()
    for p in projects:
        ws = p.get("workspace_dir", "")
        if ws and pm.delete_chat(ws, chat_id):
            return {"status": "deleted"}
    raise HTTPException(404, "Chat not found")


@app.put("/api/chats/{chat_id}")
async def update_chat(chat_id: str, body: dict, workspace: str = ""):
    from research_agent import project_manager as pm
    if workspace:
        pm.update_chat(workspace, chat_id, body)
        return {"status": "ok"}
    projects = pm.list_projects()
    for p in projects:
        ws = p.get("workspace_dir", "")
        if ws:
            chat = pm.load_chat(ws, chat_id)
            if chat:
                pm.update_chat(ws, chat_id, body)
                return {"status": "ok"}
    raise HTTPException(404, "Chat not found")
    updates = {}
    if "title" in body:
        updates["title"] = body["title"]
    if "workspace_dir" in body:
        updates["workspace_dir"] = body["workspace_dir"]
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    if not pm.update_chat(workspace, chat_id, updates):
        raise HTTPException(404, "Chat not found")
    chat = pm.load_chat(workspace, chat_id)
    return chat


@app.get("/api/progress")
async def get_progress(workspace: str = ""):
    if not workspace:
        return {"content": ""}
    from research_agent import project_manager as pm
    content = pm.load_progress(workspace)
    return {"content": content}


@app.post("/api/confirm")
async def confirm_action(body: dict):
    confirm_id = body.get("confirm_id", "")
    approved = body.get("approved", False)
    for state in _active_states.values():
        if confirm_id in state._pending_confirms:
            state._pending_confirms[confirm_id]["approved"] = approved
            state._pending_confirms[confirm_id]["event"].set()
            return {"status": "ok"}
    raise HTTPException(404, "Confirmation not found")


@app.get("/api/diagnostics")
async def get_diagnostics(limit: int = 20):
    """Developer-facing diagnostics: recent faults + session summaries + report.

    Rule-only aggregation (no LLM) so it is cheap and always available.
    """
    try:
        from research_agent.diagnostics import scan as diag_scan
        from research_agent.diagnostics import report as diag_report
        result = diag_scan.scan(limit=min(limit, 100))
        latest = diag_report.latest_report()
        return {
            "totals": result["totals"],
            "sessions": result["sessions"],
            "latest_report": latest,
        }
    except Exception as e:
        raise HTTPException(500, f"diagnostics failed: {e}")


if __name__ == "__main__":
    import uvicorn
    print(f"PaperPilot API at http://localhost:8050")
    uvicorn.run(app, host="0.0.0.0", port=8050)
