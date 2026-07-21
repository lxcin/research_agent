"""Chroma vector database wrapper."""
import os
import chromadb

from research_agent.config import get_data_dir

_COLLECTIONS: dict[str, chromadb.Collection] = {}
_EMBEDDING_FN = None
_EMBEDDING_MODEL = None


def get_embedding_model():
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        from sentence_transformers import SentenceTransformer
        model_name = os.environ.get("RESEARCH_AGENT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
        try:
            _EMBEDDING_MODEL = SentenceTransformer(model_name, local_files_only=True)
        except Exception:
            try:
                _EMBEDDING_MODEL = SentenceTransformer(model_name)
            except Exception:
                from chromadb.utils import embedding_functions
                _EMBEDDING_FN = embedding_functions.DefaultEmbeddingFunction()
                return None
    return _EMBEDDING_MODEL


def get_embedding_function():
    """Return a Chroma-compatible embedding function."""
    model = get_embedding_model()
    if model is None:
        return _EMBEDDING_FN
    from chromadb import EmbeddingFunction

    class STEmbedding(EmbeddingFunction):
        def __call__(self, input):
            return model.encode(input).tolist()

    return STEmbedding()


def get_collection(name: str = "research_chunks") -> chromadb.Collection:
    if name not in _COLLECTIONS:
        chroma_path = str(get_data_dir() / "chroma_db")
        client = chromadb.PersistentClient(path=chroma_path)
        try:
            _COLLECTIONS[name] = client.get_collection(name=name)
        except Exception:
            _COLLECTIONS[name] = client.create_collection(
                name=name,
                embedding_function=get_embedding_function(),
            )
    return _COLLECTIONS[name]


def add_chunks(paper_id: str, chunks: list[dict], collection_name: str = "research_chunks"):
    coll = get_collection(collection_name)
    ids = [f"{paper_id}_chunk_{c['chunk_index']}" for c in chunks]
    documents = [c["text"] for c in chunks]
    metadatas = [{"paper_id": paper_id, "chunk_index": c["chunk_index"]} for c in chunks]
    if ids:
        coll.upsert(ids=ids, documents=documents, metadatas=metadatas)


def search(query: str, n_results: int = 5) -> dict:
    coll = get_collection()
    return coll.query(query_texts=[query], n_results=n_results)


def delete_paper(paper_id: str):
    coll = get_collection()
    results = coll.get(where={"paper_id": paper_id})
    if results["ids"]:
        coll.delete(ids=results["ids"])


def add_paper_summary(paper_id: str, title: str, abstract: str, authors: list[str], year: int, doi: str = ""):
    """Store paper-level summary in ChromaDB for title/abstract search."""
    coll = get_collection()
    summary_text = f"Title: {title}\nAuthors: {', '.join(authors[:5])}\nYear: {year}\nAbstract: {abstract}"
    meta = {
        "paper_id": paper_id,
        "chunk_index": -1,  # -1 = paper summary
        "title": title,
        "authors": ", ".join(authors[:5]),
        "year": year,
        "doi": doi,
    }
    coll.upsert(ids=[f"{paper_id}_summary"], documents=[summary_text], metadatas=[meta])