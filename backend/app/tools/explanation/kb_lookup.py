"""知识库检索工具（explanation 层）。

封装 Chroma 向量检索，供 orchestrator 在规划或执行步骤中
查询与当前任务相关的 GEE API 文档和知识。
"""
from backend.app.agents.tools_kb import kb_search


def knowledge_base_lookup(query: str, k: int = 3) -> str:
    """
    从 Chroma 知识库检索与 query 相关的文档片段。

    Parameters
    ----------
    query : str
        检索问题或关键词。
    k : int
        返回的文档片段数量。

    Returns
    -------
    拼接后的文本字符串，或"（未找到相关文档）"。
    """
    return kb_search(query, k=k)
