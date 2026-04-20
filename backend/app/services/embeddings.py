"""Embeddings：文本向量化，供 Chroma 使用。

默认使用本地 sentence-transformers 模型，避免没有 OPENAI_API_KEY 时退化成
hash 占位向量。需要使用 OpenAI embedding 时，可设置
EMBEDDING_PROVIDER=openai。
"""
from functools import lru_cache
import os
from pathlib import Path
from typing import List

EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "local").strip().lower()
OPENAI_EMBED_MODEL = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
LOCAL_EMBED_MODEL = os.environ.get("LOCAL_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
LOCAL_EMBED_DIM = 384
OPENAI_EMBED_DIM = 1536
EMBED_DIM = OPENAI_EMBED_DIM if EMBEDDING_PROVIDER == "openai" else LOCAL_EMBED_DIM


def _get_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


@lru_cache(maxsize=1)
def _get_local_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_resolve_local_model_path(), local_files_only=True)


def _resolve_local_model_path() -> str:
    configured = Path(LOCAL_EMBED_MODEL).expanduser()
    if configured.exists():
        return str(configured)

    cache_root = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--sentence-transformers--all-MiniLM-L6-v2"
        / "snapshots"
    )
    if LOCAL_EMBED_MODEL == "sentence-transformers/all-MiniLM-L6-v2" and cache_root.exists():
        snapshots = sorted(p for p in cache_root.iterdir() if p.is_dir())
        if snapshots:
            return str(snapshots[-1])

    return LOCAL_EMBED_MODEL


def _local_embeddings(texts: List[str]) -> List[List[float]]:
    model = _get_local_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [vec.astype(float).tolist() for vec in vectors]


def _openai_embeddings(texts: List[str]) -> List[List[float]]:
    resp = _get_client().embeddings.create(input=texts, model=OPENAI_EMBED_MODEL)
    return [item.embedding for item in resp.data]


def get_embedding(text: str) -> List[float]:
    """将单条文本转为语义向量。"""
    return get_embeddings([text])[0]


def get_embeddings(texts: List[str]) -> List[List[float]]:
    """批量向量化，供 add_documents 使用。"""
    if not texts:
        return []
    if EMBEDDING_PROVIDER == "openai":
        try:
            return _openai_embeddings(texts)
        except Exception:
            # Keep the app usable during demos if the API key/network is missing.
            pass
    try:
        return _local_embeddings(texts)
    except Exception:
        # Last-resort fallback: retrieval quality is poor, but the app remains up.
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
