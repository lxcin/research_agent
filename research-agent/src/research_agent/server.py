"""FastAPI server for PaperPilot research agent frontend."""
import json
import uuid
import asyncio
import os
import threading
import queue
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import tempfile

from research_agent.agent import AgentState, run_agent
import litellm

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
    project_id: str | None = None
    config: ApiConfig | None = None


class ProjectCreate(BaseModel):
    name: str


class ProjectUpdate(BaseModel):
    name: str | None = None
    summary: str | None = None
    status: str | None = None
    workspace_dir: str | None = None


_projects: list[dict] = []


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    user_config = req.config

    if not user_config or not user_config.apiKey:
        async def gen():
            yield f"data: {json.dumps({'type': 'error', 'text': '请先配置 API Key'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    async def gen():
        yield f"data: {json.dumps({'type': 'start', 'id': str(uuid.uuid4())})}\n\n"

        q = queue.Queue()

        def emit(et: str, d: dict):
            q.put({"type": et, **d})

        def _run():
            try:
                os.environ["LLM_API_KEY"] = user_config.apiKey
                os.environ["DEEPSEEK_API_KEY"] = user_config.apiKey
                os.environ["OPENAI_API_KEY"] = user_config.apiKey

                model = user_config.model or "deepseek-chat"
                if user_config.provider == "deepseek":
                    if not model.startswith("openai/"):
                        model = "openai/" + model
                kwargs = {}
                if user_config.baseUrl:
                    kwargs["api_base"] = user_config.baseUrl

                class CustomLLM:
                    def __init__(self):
                        self.model = model
                        self.api_key = user_config.apiKey
                        self._kwargs = kwargs
                    def complete(self, messages, max_tokens=300, **kw):
                        import litellm as _llm
                        resp = _llm.completion(
                            model=self.model, messages=messages,
                            max_tokens=max_tokens, temperature=0.7,
                            api_key=self.api_key, **self._kwargs, **kw,
                        )
                        return resp.choices[0].message.content

                llm = CustomLLM()
                state = AgentState(user_input=req.message)
                result = run_agent(req.message, llm, state, on_event=emit)

                if result.retrieved_chunks:
                    sources = list({c.get('paper_id', '') for c in result.retrieved_chunks if c.get('paper_id')})
                    emit("sources", {"text": f"已搜索到 {len(result.retrieved_chunks)} 个片段，来自 {len(sources)} 篇论文"})
                    paper_info = []
                    seen = set()
                    for c in result.retrieved_chunks:
                        pid = c.get("paper_id", "")
                        if pid and pid not in seen:
                            seen.add(pid)
                            from research_agent.store import get_paper as db_get_paper
                            from research_agent.vector_store import get_collection as get_vcoll
                            vcoll = get_vcoll()
                            for pid in list(seen):
                                # Try summary doc first
                                try:
                                    result = vcoll.get(ids=[f"{pid}_summary"])
                                    if result and result["metadatas"]:
                                        m = result["metadatas"][0]
                                        paper_info.append({
                                            "id": pid,
                                            "title": m.get("title", pid)[:120],
                                            "authors": (m.get("authors", "").split(", ") if m.get("authors") else []),
                                            "year": m.get("year", 0),
                                            "abstract": (result["documents"][0] if result["documents"] else "")[:300],
                                            "doi": m.get("doi", ""),
                                        })
                                        continue
                                except Exception:
                                    pass
                                # Fallback to SQLite
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


@app.get("/api/projects")
async def list_projects():
    return _projects


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    from research_agent.store import get_project as db_get_project
    proj = db_get_project(project_id)
    if proj:
        return {"id": proj.id, "name": proj.topic, "workspace_dir": proj.workspace_dir,
                "status": proj.status.value if hasattr(proj.status, 'value') else str(proj.status)}
    for p in _projects:
        if p["id"] == project_id:
            return p
    raise HTTPException(404, "Project not found")


@app.post("/api/projects")
async def create_project(req: ProjectCreate):
    proj = {
        "id": str(uuid.uuid4())[:8],
        "name": req.name,
        "status": "active",
        "updated": "刚刚",
        "created": datetime.now().strftime("%Y-%m-%d"),
        "summary": "",
        "progress": 0,
        "steps": [],
    }
    _projects.insert(0, proj)
    return proj


@app.put("/api/projects/{project_id}")
async def update_project(project_id: str, req: ProjectUpdate):
    from research_agent.store import get_project, insert_project
    proj = get_project(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    if req.name is not None:
        proj.topic = req.name
    if req.workspace_dir is not None:
        proj.workspace_dir = req.workspace_dir
        # Ensure directory exists
        import os
        os.makedirs(req.workspace_dir, exist_ok=True)
    insert_project(proj)
    return {"id": proj.id, "topic": proj.topic, "workspace_dir": proj.workspace_dir}


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
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith('.pdf'):
        raise HTTPException(400, "Only PDF files allowed")
    from research_agent.ingestion import ingest_pdf
    import shutil
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        paper, msg = ingest_pdf(tmp_path)
        if paper:
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


@app.get("/api/projects/{project_id}/papers")
async def get_project_papers(project_id: str):
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


@app.get("/api/project-files/{project_id}/{filename:path}")
async def serve_project_file(project_id: str, filename: str):
    """Serve static files from project directory (HTML, images, etc.)."""
    from research_agent.config import get_data_dir
    proj_dir = get_data_dir() / "projects" / project_id
    file_path = proj_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "File not found")
    # Security: only serve from project dir
    resolved = file_path.resolve()
    if not str(resolved).startswith(str(proj_dir.resolve())):
        raise HTTPException(403)
    return FileResponse(file_path)


@app.get("/api/workspace/{project_id}")
async def list_workspace(project_id: str):
    """List files in project working directory (max depth 3)."""
    from research_agent.config import get_data_dir
    import os as _os
    proj_dir = get_data_dir() / "projects" / project_id
    if not proj_dir.exists():
        return {"project_id": project_id, "dir": str(proj_dir), "files": []}

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
    return {"project_id": project_id, "dir": str(proj_dir), "files": files[:100], "count": len(files)}


if __name__ == "__main__":
    import uvicorn
    print(f"PaperPilot API at http://localhost:8050")
    uvicorn.run(app, host="0.0.0.0", port=8050)