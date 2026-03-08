"""RAG 链：检索 + LLM 生成。"""
from backend.app.rag.prompts import SYSTEM_PROMPT_GEE_ASSISTANT
from backend.app.rag.retriever import get_relevant_docs
from backend.app.services import llm_client


async def run_rag(query: str) -> str:
    """
    基础 RAG：检索相关文档，拼进 prompt，调用 LLM 得到回答。
    """
    docs = get_relevant_docs(query, k=3)
    context = "\n\n---\n\n".join(docs) if docs else "（暂无相关文档）"
    prompt = f"""{SYSTEM_PROMPT_GEE_ASSISTANT}

参考知识库内容：
{context}

用户问题：{query}

请基于以上内容回答。"""
    return await llm_client.chat_with_llm(prompt)
