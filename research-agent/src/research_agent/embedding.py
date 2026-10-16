"""Optional embedding layer for semantic recall (episodic-memory vector search).

Uses sentence-transformers; when no model is available this degrades to no-ops and
recall falls back to keyword ranking. Enabled via RESEARCH_AGENT_EMBEDDING=1
(legacy RESEARCH_AGENT_MEMORY_VECTOR=1 is also honored) so the default — and all
tests — never trigger a remote model download.
"""
from __future__ import annotations

import os
import threading

_model_lock = threading.Lock()
_model = None
_model_checked = False
_AVAILABLE = None

# bge-zh retrieval: prefix the QUERY (not the passages), per the model card.
_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def _load_model():
    global _model, _model_checked
    if _model_checked:
        return _model
    with _model_lock:
        if _model_checked:
            return _model
        try:
            from sentence_transformers import SentenceTransformer
            name = os.environ.get("RESEARCH_AGENT_EMBEDDING_MODEL",
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
    """Embedding layer enabled only when explicitly opted in AND the model loads."""
    global _AVAILABLE
    if _AVAILABLE is None:
        enabled = (os.environ.get("RESEARCH_AGENT_EMBEDDING", "0") == "1"
                   or os.environ.get("RESEARCH_AGENT_MEMORY_VECTOR", "0") == "1")
        _AVAILABLE = enabled and _load_model() is not None
    return _AVAILABLE


def set_available(flag: bool):
    """Test hook: force availability on/off."""
    global _AVAILABLE
    _AVAILABLE = flag


def encode_query(text: str):
    """Embed a query with the cached model. Returns 1-D array or None."""
    model = _load_model()
    if model is None:
        return None
    try:
        return model.encode([_QUERY_INSTRUCTION + text], normalize_embeddings=True)[0]
    except Exception:
        return None


def encode_documents(texts: list[str]):
    """Embed documents (no query instruction), normalized. NxD array or None."""
    model = _load_model()
    if model is None:
        return None
    try:
        return model.encode(list(texts), normalize_embeddings=True)
    except Exception:
        return None
