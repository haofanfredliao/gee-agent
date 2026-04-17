"""Chroma 向量库：存储与检索 GEE 文档/代码片段。"""
from pathlib import Path
from typing import Any, Dict, List, Optional

def _get_persist_dir() -> Path:
    import os
    try:
        from backend.app.core.config import CHROMA_PERSIST_DIR
        default = CHROMA_PERSIST_DIR
    except Exception:
        default = str(Path(__file__).resolve().parents[4] / "data" / "chroma")
    return Path(os.environ.get("CHROMA_PERSIST_DIR", default))


def _get_client():
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
    except ImportError:
        return None
    persist = _get_persist_dir()
    persist.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist), settings=ChromaSettings(anonymized_telemetry=False))
    return client


_collection_name = "gee_kb"


def _collection():
    client = _get_client()
    if client is None:
        return None
    return client.get_or_create_collection(_collection_name, metadata={"description": "GEE docs and snippets"})


def reset_collection() -> None:
    """删除并重建集合，便于全量重建索引。"""
    client = _get_client()
    if client is None:
        raise RuntimeError("chromadb 未安装，请 pip install chromadb")
    try:
        client.delete_collection(_collection_name)
    except Exception:
        # 集合可能不存在；忽略后继续重建。
        pass
    client.get_or_create_collection(_collection_name, metadata={"description": "GEE docs and snippets"})


def collection_count() -> int:
    """返回当前集合中的文档数量。"""
    coll = _collection()
    if coll is None:
        return 0
    return int(coll.count())


def add_documents(docs: List[str], metadatas: Optional[List[dict]] = None) -> None:
    """将文档加入向量库（使用 sentence-transformers 显式计算 embedding）。"""
    import uuid
    from backend.app.services.embeddings import get_embeddings
    coll = _collection()
    if coll is None:
        raise RuntimeError("chromadb 未安装，请 pip install chromadb")
    metadatas = metadatas or [{}] * len(docs)
    if len(metadatas) != len(docs):
        metadatas = [{}] * len(docs)
    ids = [f"doc_{uuid.uuid4().hex[:12]}" for _ in range(len(docs))]
    embeddings = get_embeddings(docs)
    coll.add(documents=docs, metadatas=metadatas, ids=ids, embeddings=embeddings)


def similarity_search(query: str, k: int = 3) -> List[Dict[str, Any]]:
    """
    相似度检索，返回文档与 metadata 列表。
    每项为 {"content": str, "metadata": dict}。
    """
    coll = _collection()
    if coll is None:
        return []
    from backend.app.services.embeddings import get_embedding
    query_vec = get_embedding(query)
    result = coll.query(query_embeddings=[query_vec], n_results=k, include=["documents", "metadatas"])
    out = []
    if result["documents"] and result["documents"][0]:
        for doc, meta in zip(result["documents"][0], (result.get("metadatas") or [[]])[0] or []):
            out.append({"content": doc, "metadata": meta or {}})
    return out
