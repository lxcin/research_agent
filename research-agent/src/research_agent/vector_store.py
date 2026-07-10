"""Chroma vector database wrapper."""
import os
import chromadb
from chromadb.utils import embedding_functions

from research_agent.config import get_data_dir

_COLLECTION = None
_EMBEDDING_FN = None


def get_embedding_fn():
    global _EMBEDDING_FN
    if _EMBEDDING_FN is None:
        _EMBEDDING_FN = embedding_functions.DefaultEmbeddingFunction()
    return _EMBEDDING_FN


def get_collection() -> chromadb.Collection:
    global _COLLECTION
    if _COLLECTION is None:
        chroma_path = str(get_data_dir() / "chroma_db")
        client = chromadb.PersistentClient(path=chroma_path)
        _COLLECTION = client.get_or_create_collection(
            name="research_chunks",
            embedding_function=get_embedding_fn(),
        )
    return _COLLECTION


def add_chunks(paper_id: str, chunks: list[dict]):
    coll = get_collection()
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