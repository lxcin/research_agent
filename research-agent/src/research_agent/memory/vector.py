"""Optional vector layer for Tier B memory.

Uses an explicit embedding model (sentence-transformers) and ALWAYS passes
precomputed embeddings to Chroma (upsert/query) so Chroma's default ONNX
embedder is never instantiated. When no embedding model is available, this
module degrades to no-ops and retrieval falls back to keyword ranking.
"""
import os
import threading

from research_agent.config import get_data_dir

_model_lock = threading.Lock()
_model = None
_model_checked = False
_coll = None
_coll_lock = threading.Lock()
_COLLECTION_NAME = "memory_units"

# Module-level flag readable/toggleable for tests.
_AVAILABLE = None  # None=unknown, True, False


def _load_model():
    global _model, _model_checked
    if _model_checked:
        return _model
    with _model_lock:
        if _model_checked:
            return _model
        try:
            from sentence_transformers import SentenceTransformer
            name = os.environ.get("RESEARCH_AGENT_MEMORY_EMBEDDING_MODEL",
                                  "BAAI/bge-small-zh-v1.5")
            try:
                _model = SentenceTransformer(name, local_files_only=True)
            except Exception:
                try:
                    _model = SentenceTransformer(name)
                except Exception:
                    _model = None
        except Exception:
            _model = None
        _model_checked = True
        return _model


def is_available() -> bool:
    """Vector layer enabled only when explicitly opted in AND model loads.

    Gated by RESEARCH_AGENT_MEMORY_VECTOR=1 so the default (and all tests)
    never trigger a remote model download. Set to 1 when embeddings available.
    """
    global _AVAILABLE
    if _AVAILABLE is None:
        enabled = os.environ.get("RESEARCH_AGENT_MEMORY_VECTOR", "0") == "1"
        _AVAILABLE = enabled and _load_model() is not None
    return _AVAILABLE


def set_available(flag: bool):
    """Test hook: force availability on/off."""
    global _AVAILABLE
    _AVAILABLE = flag


def _get_collection():
    global _coll
    if not is_available():
        return None
    if _coll is not None:
        return _coll
    with _coll_lock:
        if _coll is not None:
            return _coll
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(get_data_dir() / "chroma_db"))
            # Explicit embedding_function: never let Chroma pick its ONNX default.
            ef = _ManualEmbeddingFunction()
            try:
                _coll = client.get_collection(name=_COLLECTION_NAME,
                                              embedding_function=ef)
            except Exception:
                _coll = client.create_collection(name=_COLLECTION_NAME,
                                                 embedding_function=ef,
                                                 metadata={"hnsw:space": "cosine"})
        except Exception:
            _coll = None
        return _coll


class _ManualEmbeddingFunction:
    """Chroma EmbeddingFunction backed by our cached model."""

    def __call__(self, input):
        model = _load_model()
        if model is None:
            raise RuntimeError("embedding model unavailable")
        return model.encode(input).tolist()

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        pass


def add(unit_id: str, text: str, metadata: dict | None = None) -> str | None:
    """Embed + index one unit. Returns chroma id, or None when degraded."""
    coll = _get_collection()
    if coll is None:
        return None
    try:
        coll.upsert(ids=[unit_id], documents=[text],
                    metadatas=[metadata or {}])
        return unit_id
    except Exception:
        return None


def remove(unit_id: str):
    coll = _get_collection()
    if coll is None:
        return
    try:
        coll.delete(ids=[unit_id])
    except Exception:
        pass


def query(text: str, n_results: int = 5) -> list[dict]:
    """Vector search. Returns [{id, distance}]. [] when degraded or empty."""
    coll = _get_collection()
    if coll is None:
        return []
    try:
        res = coll.query(query_texts=[text], n_results=n_results)
    except Exception:
        return []
    if not res or not res["ids"] or not res["ids"][0]:
        return []
    return [
        {"id": res["ids"][0][i],
         "distance": res["distances"][0][i] if res.get("distances") else 1.0}
        for i in range(len(res["ids"][0]))
    ]
