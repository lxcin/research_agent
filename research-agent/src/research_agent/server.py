"""FastAPI API server for PaperPilot (API only — no bundled frontend)."""
import json
import uuid
import asyncio
import os
import threading
import queue
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from research_agent.agent import AgentState, run_agent_resilient as run_agent

_active_states: dict[str, AgentState] = {}
_active_lock = threading.Lock()

@asynccontextmanager
async def _lifespan(app):
    from research_agent.tools import get_registry
    manager = get_registry().background_tasks
    manager.resume()
    from research_agent.git_maintenance import resume as resume_git_maintenance
    resume_git_maintenance()
    from research_agent.checkpoint_events import resume as resume_snapshot_events
    await asyncio.to_thread(resume_snapshot_events)
    try:
        yield
    finally:
        with _active_lock:
            states = list(_active_states.values())
        for state in states:
            state._cancellation.cancel()
            for pending in list(state._pending_confirms.values()):
                pending["event"].set()
        await asyncio.to_thread(manager.shutdown)


app = FastAPI(title="PaperPilot API", lifespan=_lifespan)

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
    execution_id: str | None = None
    isolate_session: bool | None = None


@app.get("/api/health")
async def health():
    return {"status": "ok"}


class UndoReviewRequest(BaseModel):
    workspace_dir: str = ""
    paths: list[str] | None = None
    resolved: list[str] = Field(default_factory=list)


class UndoApplyRequest(BaseModel):
    workspace_dir: str = ""
    review_token: str


def _undo_workspace(chat_id, requested):
    from research_agent.session_workspace import bound_session, canonical
    from research_agent.review_baseline import baseline
    try:
        binding = bound_session(chat_id)
        if binding:
            if binding.archived:
                raise ValueError("Restore the archived session before undo")
            if requested and canonical(requested) not in {canonical(binding.project), canonical(binding.workspace)}:
                raise ValueError("Workspace does not belong to this session")
            workspace = binding.workspace
        else:
            workspace = requested
        active = baseline(workspace, "active-turn") if workspace else {}
        if not active or active.get("chat_id") != chat_id:
            raise ValueError("No current turn snapshot belongs to this chat/workspace")
        return workspace, active["ref"]
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/chats/{chat_id}/undo/review")
async def review_chat_undo(chat_id: str, req: UndoReviewRequest):
    from research_agent.rollback import review_undo
    workspace, ref = _undo_workspace(chat_id, req.workspace_dir)
    result = await asyncio.to_thread(review_undo, workspace, ref, req.paths, resolved=req.resolved)
    if not result["success"]:
        raise HTTPException(409, result)
    return result


@app.post("/api/chats/{chat_id}/undo/apply")
async def apply_chat_undo(chat_id: str, req: UndoApplyRequest):
    from research_agent.rollback import apply_undo
    workspace, ref = _undo_workspace(chat_id, req.workspace_dir)
    result = await asyncio.to_thread(apply_undo, workspace, review_token=req.review_token, expected_ref=ref)
    if not result["success"]:
        raise HTTPException(409, result)
    return result


@app.post("/api/chats/{chat_id}/undo/cancel")
async def cancel_chat_undo(chat_id: str, req: UndoApplyRequest):
    from research_agent.rollback import cancel_conflict
    workspace, ref = _undo_workspace(chat_id, req.workspace_dir)
    result = await asyncio.to_thread(cancel_conflict, workspace, review_token=req.review_token, expected_ref=ref)
    if not result["success"]:
        raise HTTPException(409, result)
    return result


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
                chat = req.chat_id or ("chat_" + uuid.uuid4().hex[:12] if req.isolate_session else "")
                state.active_chat_id = chat
                state.workspace_dir = workspace
                from research_agent.execution import CancellationToken
                token = CancellationToken()
                execution_id = req.execution_id or uuid.uuid4().hex
                with _active_lock:
                    if execution_id in _active_states:
                        raise RuntimeError("Execution already active")
                    state._cancellation = token
                    _active_states[execution_id] = state
                emit("execution", {"execution_id": execution_id})
                try:
                    result = run_agent(req.message, llm, state, on_event=emit,
                                       workspace_dir=workspace, chat_id=chat,
                                       cancellation=token, execution_id=execution_id, isolate_session=req.isolate_session)
                    if result.error:
                        emit("error", {"text": result.error, "termination_reason": result.termination_reason})
                finally:
                    with _active_lock:
                        _active_states.pop(execution_id, None)

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
        if not pm.load_chat(workspace, chat_id):
            raise HTTPException(404, "Chat not found")
        await close_chat_session(chat_id)
        await asyncio.to_thread(pm.delete_chat, workspace, chat_id)
        return {"status": "deleted"}
    projects = pm.list_projects()
    for p in projects:
        ws = p.get("workspace_dir", "")
        if ws and pm.load_chat(ws, chat_id):
            await close_chat_session(chat_id)
            await asyncio.to_thread(pm.delete_chat, ws, chat_id)
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
    with _active_lock:
        states = list(_active_states.values())
    for state in states:
        if confirm_id in state._pending_confirms:
            state._pending_confirms[confirm_id]["approved"] = approved
            state._pending_confirms[confirm_id]["event"].set()
            return {"status": "ok"}
    raise HTTPException(404, "Confirmation not found")


@app.get("/api/executions/{execution_id}")
async def inspect_execution(execution_id: str):
    from research_agent.recovery import inspect_execution as inspect
    try:
        return await asyncio.to_thread(inspect, execution_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str):
    from research_agent.tools import get_registry
    tasks = await asyncio.to_thread(get_registry().background_tasks.cancel, execution=execution_id)
    with _active_lock:
        state = _active_states.get(execution_id)
        if state is None:
            if not tasks:
                raise HTTPException(404, "Execution not active")
        else:
            state._cancellation.cancel()
            for pending in list(state._pending_confirms.values()):
                pending["event"].set()
    return {"status": "cancellation_requested", "execution_id": execution_id, "background_tasks": tasks}


@app.get("/api/chats/{chat_id}/tasks")
async def list_background_tasks(chat_id: str):
    from research_agent.tools import get_registry
    tasks = await asyncio.to_thread(get_registry().background_tasks.list, chat_id)
    return {"tasks": tasks}


@app.post("/api/chats/{chat_id}/tasks/{task_id}/cancel")
async def cancel_background_task(chat_id: str, task_id: str):
    from research_agent.tools import get_registry
    tasks = await asyncio.to_thread(get_registry().background_tasks.cancel, chat=chat_id, task_id=task_id)
    if not tasks:
        raise HTTPException(404, "No active task in this session")
    return {"status": "cancellation_requested", "task_id": task_id}


@app.post("/api/chats/{chat_id}/close")
async def close_chat_session(chat_id: str):
    from research_agent.tools import get_registry
    with _active_lock:
        for state in _active_states.values():
            if state.active_chat_id == chat_id:
                state._cancellation.cancel()
                for pending in list(state._pending_confirms.values()):
                    pending["event"].set()
    try:
        await asyncio.to_thread(get_registry().close_session, chat_id, 10)
    except (TimeoutError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "closed", "chat_id": chat_id}


@app.post("/api/chats/{chat_id}/reopen")
async def reopen_chat_session(chat_id: str):
    from research_agent.tools import get_registry
    try:
        get_registry().reopen_session(chat_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "open", "chat_id": chat_id}


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
