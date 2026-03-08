"""检索器：从 Chroma 获取与 query 相关的文档。"""
from typing import List

from backend.app.services import chroma_store


def get_relevant_docs(query: str, k: int = 3) -> List[str]:
    """返回与 query 最相关的文档内容列表（供 LangChain/RAG 使用）。"""
    hits = chroma_store.similarity_search(query, k=k)
    return [h["content"] for h in hits]
