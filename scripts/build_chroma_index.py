"""构建 Chroma 知识库：将 gee_rag_data 下文档全量加载到向量库。"""
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# 确保项目根在 path 中
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "gee_rag_data"
SUPPORTED_SUFFIXES = {".txt", ".md", ".json", ".jsonl"}
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
BATCH_SIZE = 128


def iter_data_files() -> Iterable[Path]:
    for path in sorted(DATA_DIR.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            yield path


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: List[str] = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            breakpoints = (
                cleaned.rfind("\n\n", start, end),
                cleaned.rfind("\n", start, end),
                cleaned.rfind(". ", start, end),
                cleaned.rfind("。", start, end),
            )
            best = max(breakpoints)
            if best > start + 200:
                end = best + 1
        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _json_item_to_text(item: object) -> Tuple[str, Dict[str, str]]:
    if isinstance(item, dict):
        name = str(item.get("name") or item.get("id") or "").strip()
        desc = str(item.get("description") or item.get("summary") or "").strip()
        usage = str(item.get("usage") or "").strip()
        returns = str(item.get("returns") or "").strip()
        args = item.get("arguments")
        arg_text = ""
        if isinstance(args, list) and args:
            arg_lines = []
            for arg in args:
                if isinstance(arg, dict):
                    arg_name = str(arg.get("name") or "").strip()
                    arg_desc = str(arg.get("description") or "").strip()
                    if arg_name or arg_desc:
                        arg_lines.append(f"- {arg_name}: {arg_desc}".strip())
                else:
                    arg_lines.append(f"- {str(arg).strip()}")
            arg_text = "\n".join(x for x in arg_lines if x)

        parts = []
        if name:
            parts.append(f"API: {name}")
        if desc:
            parts.append(f"Description: {desc}")
        if usage:
            parts.append(f"Usage: {usage}")
        if returns:
            parts.append(f"Returns: {returns}")
        if arg_text:
            parts.append(f"Arguments:\n{arg_text}")

        if not parts:
            return "", {}
        metadata = {}
        if name:
            metadata["api_name"] = name
        if "source" in item and item.get("source"):
            metadata["source"] = str(item["source"])
        return "\n".join(parts), metadata

    if item is None:
        return "", {}
    return str(item).strip(), {}


def parse_json_file(path: Path) -> List[Tuple[str, Dict[str, str]]]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []

    docs: List[Tuple[str, Dict[str, str]]] = []
    if path.suffix.lower() == ".jsonl":
        for line_no, line in enumerate(content.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            text, meta = _json_item_to_text(obj)
            if text:
                meta["line_no"] = str(line_no)
                docs.append((text, meta))
        return docs

    obj = json.loads(content)
    if isinstance(obj, list):
        for idx, item in enumerate(obj):
            text, meta = _json_item_to_text(item)
            if text:
                meta["item_index"] = str(idx)
                docs.append((text, meta))
    else:
        text, meta = _json_item_to_text(obj)
        if text:
            docs.append((text, meta))
    return docs


def parse_text_file(path: Path) -> List[Tuple[str, Dict[str, str]]]:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        return []

    sections = [x.strip() for x in re.split(r"\n={3,}\n", text) if x.strip()]
    if len(sections) <= 1:
        sections = [text]

    docs: List[Tuple[str, Dict[str, str]]] = []
    for section_idx, section in enumerate(sections):
        chunks = split_text(section)
        for chunk_idx, chunk in enumerate(chunks):
            docs.append(
                (
                    chunk,
                    {
                        "section_index": str(section_idx),
                        "chunk_index": str(chunk_idx),
                    },
                )
            )
    return docs


def collect_documents() -> Tuple[List[str], List[dict]]:
    documents: List[str] = []
    metadatas: List[dict] = []
    files = list(iter_data_files())
    if not files:
        raise FileNotFoundError(f"未在 {DATA_DIR} 发现可加载文档")

    for file_path in files:
        if file_path.suffix.lower() in {".json", ".jsonl"}:
            pairs = parse_json_file(file_path)
        else:
            pairs = parse_text_file(file_path)

        rel = str(file_path.relative_to(ROOT))
        for text, meta in pairs:
            documents.append(text)
            merged_meta = {"source_file": rel}
            merged_meta.update(meta)
            metadatas.append(merged_meta)
    return documents, metadatas


def ingest_in_batches(documents: List[str], metadatas: List[dict]) -> None:
    from backend.app.services.chroma_store import add_documents

    for i in range(0, len(documents), BATCH_SIZE):
        docs_batch = documents[i : i + BATCH_SIZE]
        metas_batch = metadatas[i : i + BATCH_SIZE]
        add_documents(docs_batch, metadatas=metas_batch)
        print(f"[ingest] {min(i + BATCH_SIZE, len(documents))}/{len(documents)}")


def main() -> None:
    from backend.app.services.chroma_store import collection_count, reset_collection

    print(f"[start] loading from {DATA_DIR}")
    documents, metadatas = collect_documents()
    print(f"[prepare] docs={len(documents)}")
    reset_collection()
    ingest_in_batches(documents, metadatas)
    print(f"[done] collection_count={collection_count()}")


if __name__ == "__main__":
    main()
