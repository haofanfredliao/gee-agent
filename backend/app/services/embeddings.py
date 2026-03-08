"""Embeddings：文本向量化，供 Chroma 使用。"""
from typing import List


def get_embedding(text: str) -> List[float]:
    """
    将文本转为向量。
    未配置 OpenAI/其他 API 时使用简单占位向量（固定维度）。
    """
    # TODO: 接入 OpenAI embeddings 或其它服务
    # import openai
    # r = openai.Embedding.create(input=text, model="text-embedding-ada-002")
    # return r["data"][0]["embedding"]
    dim = 384
    h = hash(text) % (2 ** 32)
    return [((h + i) % 1000) / 1000.0 - 0.5 for i in range(dim)]
