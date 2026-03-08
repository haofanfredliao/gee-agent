"""知识库 Tool：Chroma 检索。"""
from backend.app.services import chroma_store


def kb_search(query: str, k: int = 3) -> str:
    """从知识库检索相关文档，返回拼接后的文本。"""
    hits = chroma_store.similarity_search(query, k=k)
    if not hits:
        return "（未找到相关文档）"
    return "\n\n".join(h["content"] for h in hits)
