"""Embeddings：文本向量化，供 Chroma 使用（sentence-transformers all-MiniLM-L6-v2）。

模型首次调用时懒加载，不可用时自动回退为 hash 占位向量，保证整体服务不因模型加载失败而中断。
"""
from typing import List

_model = None  # 懒加载单例


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def get_embedding(text: str) -> List[float]:
    """将单条文本转为 384 维向量。不可用时回退为 hash 占位。"""
    try:
        return _get_model().encode(text, show_progress_bar=False).tolist()
    except Exception:
        return _hash_fallback(text)


def get_embeddings(texts: List[str]) -> List[List[float]]:
    """批量向量化，供 add_documents 使用。不可用时回退为 hash 占位。"""
    try:
        return _get_model().encode(texts, show_progress_bar=False).tolist()
    except Exception:
        return [_hash_fallback(t) for t in texts]


class GeeEmbeddingFunction:
    """
    Chromadb-compatible EmbeddingFunction wrapper。
    可传入 chromadb.get_or_create_collection(embedding_function=...) 使用。
    """

    def __call__(self, input: List[str]) -> List[List[float]]:
        return get_embeddings(input)


def _hash_fallback(text: str) -> List[float]:
    dim = 384
    h = hash(text) % (2 ** 32)
    return [((h + i) % 1000) / 1000.0 - 0.5 for i in range(dim)]
