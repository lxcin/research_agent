"""FastAPI server for PaperPilot research agent frontend."""
import json
import uuid
import asyncio
import os
import threading
import queue
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import tempfile

from research_agent.agent import AgentState, run_agent

app = FastAPI(title="PaperPilot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"

# Serve built frontend when available
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
    
    @app.get("/")
    async def serve_index():
        return FileResponse(FRONTEND_DIST / "index.html")


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
                result = run_agent(req.message, llm, state, on_event=emit,
                                   workspace_dir=workspace, chat_id=chat)

                if result.retrieved_chunks:
                    sources = list({c.get('paper_id', '') for c in result.retrieved_chunks if c.get('paper_id')})
                    emit("sources", {"text": f"已搜索到 {len(result.retrieved_chunks)} 个片段，来自 {len(sources)} 篇论文"})
                    paper_info = []
                    seen = set()
                    from research_agent.store import get_paper as db_get_paper
                    from research_agent.vector_store import get_collection as get_vcoll
                    vcoll = get_vcoll()
                    for c in result.retrieved_chunks:
                        pid = c.get("paper_id", "")
                        if pid and pid not in seen:
                            seen.add(pid)
                            try:
                                res = vcoll.get(ids=[f"{pid}_summary"])
                                if res and res["metadatas"]:
                                    m = res["metadatas"][0]
                                    paper_info.append({
                                        "id": pid,
                                        "title": m.get("title", pid)[:120],
                                        "authors": (m.get("authors", "").split(", ") if m.get("authors") else []),
                                        "year": m.get("year", 0),
                                        "abstract": (res["documents"][0] if res["documents"] else "")[:300],
                                        "doi": m.get("doi", ""),
                                    })
                                    continue
                            except Exception:
                                pass
                            p = db_get_paper(pid)
                            if p:
                                paper_info.append({
                                    "id": pid, "title": p.title[:120], "authors": p.authors[:5],
                                    "year": p.year, "abstract": p.abstract[:300], "doi": p.doi,
                                })
                            else:
                                paper_info.append({"id": pid, "title": pid[:80], "authors": [], "year": 0, "abstract": "", "doi": ""})
                    if paper_info:
                        emit("citations", {"papers": paper_info})

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


@app.get("/api/graph")
async def get_graph():
    from research_agent.knowledge_graph import load_graph
    kg = load_graph()
    nodes, edges, node_ids = [], [], set()
    for node_id in kg.graph.nodes:
        node = kg.graph.nodes[node_id]
        if node.get("type") == "paper":
            nodes.append({
                "id": node_id,
                "label": node.get("title", node_id)[:50],
                "type": "paper",
                "meta": str(node.get("year", "")),
            })
            node_ids.add(node_id)
        else:
            claim = node.get("claim")
            if claim:
                nodes.append({
                    "id": node_id,
                    "label": claim.text[:60] if claim.text else node_id,
                    "type": claim.claim_type if hasattr(claim, 'claim_type') else "viewpoint",
                    "meta": getattr(claim, 'source', ''),
                })
                node_ids.add(node_id)
    for u, v, data in kg.graph.edges(data=True):
        rel = data.get("relation_type", "extends")
        if u in node_ids and v in node_ids:
            edges.append({"source": u, "target": v, "type": rel})
    return {"nodes": nodes, "edges": edges}


@app.get("/api/graph/{paper_id}")
async def get_paper_graph(paper_id: str):
    from research_agent.knowledge_graph import build_paper_argument_tree
    from research_agent.ingestion import recall_full_paper
    from research_agent.llm import LiteLLMProvider
    text = recall_full_paper(paper_id)
    if not text:
        raise HTTPException(404, "Paper not found")
    llm = LiteLLMProvider()
    return build_paper_argument_tree(paper_id, text, llm)


@app.get("/api/papers")
async def list_papers():
    from research_agent.store import get_all_papers
    papers = get_all_papers()
    return [{
        "id": p.id, "title": p.title, "year": p.year,
        "authors": p.authors, "doi": p.doi,
        "citation_count": p.citation_count, "abstract": p.abstract[:300],
        "source_score": p.source_score,
    } for p in papers]


@app.delete("/api/papers/{paper_id}")
async def delete_paper(paper_id: str):
    from research_agent.store import delete_paper, get_paper
    from research_agent.vector_store import delete_paper as delete_vec_paper
    paper = get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    delete_paper(paper_id)       # SQLite
    delete_vec_paper(paper_id)   # ChromaDB (chunks + summary)
    # Rebuild BM25
    from research_agent.retrieval import build_bm25_index
    build_bm25_index()
    return {"status": "deleted"}


@app.post("/api/upload/pdf")
async def upload_pdf(file: UploadFile = File(...), dir: str = ""):
    ext = (file.filename or "").lower().rsplit(".", 1)[-1] if "." in (file.filename or "") else ""
    if ext not in ("pdf", "md", "txt"):
        raise HTTPException(400, "Only PDF, Markdown (.md) and text (.txt) files allowed")

    from research_agent.ingestion import ingest_pdf, ingest_text
    import shutil

    with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        if ext == "pdf":
            paper, msg = ingest_pdf(tmp_path)
        else:
            with open(tmp_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            title = file.filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
            paper, msg = ingest_text(text=text, title=title)
        if paper:
            if dir:
                from research_agent import project_manager as pm
                from research_agent.store import link_paper_to_project
                link_paper_to_project(paper.id, pm.get_project_id(dir))
            return {"status": "ok", "paper_id": paper.id, "title": paper.title, "message": msg}
        return {"status": "error", "message": msg}
    finally:
        os.unlink(tmp_path)


@app.get("/api/tools")
async def list_tools():
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    register_builtins()
    registry = get_registry()
    return [{"name": name, "description": t.description[:80], "category": t.category}
            for name, t in registry.tools.items()]


@app.get("/api/workspaces/papers")
async def get_workspace_papers(dir: str = ""):
    from research_agent import project_manager as pm
    project_id = pm.get_project_id(dir)
    from research_agent.store import get_project_papers as gpp, get_paper
    paper_ids = gpp(project_id)
    papers = []
    for pid in paper_ids:
        p = get_paper(pid)
        if p:
            papers.append({
                "id": p.id, "title": p.title, "year": p.year,
                "authors": p.authors[:3], "doi": p.doi,
            })
    return papers


@app.get("/api/workspaces/paper")
async def get_workspace_paper(dir: str = "", paper_id: str = ""):
    if not paper_id:
        raise HTTPException(400, "paper_id required")
    from research_agent.store import get_paper as gp
    from research_agent.vector_store import get_collection as get_vcoll

    p = gp(paper_id)
    if p:
        return {
            "id": p.id, "title": p.title, "year": p.year,
            "authors": p.authors, "doi": p.doi,
            "citation_count": p.citation_count, "abstract": p.abstract[:500],
            "source_score": p.source_score,
        }

    try:
        vcoll = get_vcoll()
        res = vcoll.get(ids=[f"{paper_id}_summary"])
        if res and res["metadatas"]:
            m = res["metadatas"][0]
            return {
                "id": paper_id,
                "title": m.get("title", paper_id),
                "authors": [a.strip() for a in m.get("authors", "").split(",") if a.strip()],
                "year": m.get("year", 0),
                "abstract": (res["documents"][0] if res["documents"] else "")[:500],
                "doi": m.get("doi", ""),
                "citation_count": 0,
                "source_score": 5,
            }
    except Exception:
        pass

    try:
        vcoll = get_vcoll()
        res = vcoll.get(where={"doi": f"arxiv:{paper_id}"})
        if res and res["ids"]:
            pid_db = res["ids"][0].replace("_summary", "")
            pp = gp(pid_db)
            if pp:
                return {
                    "id": pp.id, "title": pp.title, "year": pp.year,
                    "authors": pp.authors, "doi": pp.doi,
                    "citation_count": pp.citation_count, "abstract": pp.abstract[:500],
                    "source_score": pp.source_score,
                }
            m = res["metadatas"][0]
            return {
                "id": pid_db,
                "title": m.get("title", paper_id),
                "authors": [a.strip() for a in m.get("authors", "").split(",") if a.strip()],
                "year": m.get("year", 0),
                "abstract": (res["documents"][0] if res["documents"] else "")[:500],
                "doi": m.get("doi", ""),
                "citation_count": 0,
                "source_score": 5,
            }
    except Exception:
        pass

    raise HTTPException(404, "Paper not found")


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
    if not workspace:
        return []
    from research_agent import project_manager as pm
    return pm.list_chats(workspace)


@app.post("/api/chats")
async def create_chat(workspace: str = "", title: str = ""):
    if not workspace:
        raise HTTPException(400, "workspace parameter required")
    from research_agent import project_manager as pm
    chat_id = pm.create_chat(workspace, title)
    return {"chat_id": chat_id}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str, workspace: str = ""):
    if not workspace:
        raise HTTPException(400, "workspace parameter required")
    from research_agent import project_manager as pm
    chat = pm.load_chat(workspace, chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    return chat


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, workspace: str = ""):
    if not workspace:
        raise HTTPException(400, "workspace parameter required")
    from research_agent import project_manager as pm
    if not pm.delete_chat(workspace, chat_id):
        raise HTTPException(404, "Chat not found")
    return {"status": "deleted"}


@app.put("/api/chats/{chat_id}")
async def update_chat(chat_id: str, body: dict, workspace: str = ""):
    if not workspace:
        raise HTTPException(400, "workspace parameter required")
    from research_agent import project_manager as pm
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


if __name__ == "__main__":
    import uvicorn
    print(f"PaperPilot API at http://localhost:8050")
    uvicorn.run(app, host="0.0.0.0", port=8050)