"""Embeddings：文本向量化，供 Chroma 使用（OpenAI text-embedding-ada-002）。

通过环境变量 OPENAI_API_KEY 调用 OpenAI API；
API 不可用时自动回退为 hash 占位向量，保证整体服务不中断。
"""
import os
from typing import List

EMBED_MODEL = "text-embedding-ada-002"
EMBED_DIM = 1536


def _get_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def get_embedding(text: str) -> List[float]:
    """将单条文本转为 1536 维向量（ada-002）。不可用时回退为 hash 占位。"""
    try:
        resp = _get_client().embeddings.create(input=[text], model=EMBED_MODEL)
        return resp.data[0].embedding
    except Exception:
        return _hash_fallback(text)


def get_embeddings(texts: List[str]) -> List[List[float]]:
    """批量向量化，供 add_documents 使用。不可用时回退为 hash 占位。"""
    try:
        resp = _get_client().embeddings.create(input=texts, model=EMBED_MODEL)
        return [item.embedding for item in resp.data]
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
    h = hash(text) % (2 ** 32)
    return [((h + i) % 1000) / 1000.0 - 0.5 for i in range(EMBED_DIM)]
