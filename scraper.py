"""
GEE Python API 文档爬取脚本
用途：为 RAG 知识库准备 ee.* 函数文档
输出：
  - gee_api_docs.json       结构化函数文档
  - gee_api_docs_for_rag.txt  切好 chunk 的纯文本，直接喂 Chroma
"""

import json
import time
import re
import requests
from bs4 import BeautifulSoup
from pathlib import Path

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
OUTPUT_DIR = Path("./gee_rag_data")
OUTPUT_DIR.mkdir(exist_ok=True)

JSON_OUTPUT  = OUTPUT_DIR / "gee_api_docs.json"
TEXT_OUTPUT  = OUTPUT_DIR / "gee_api_docs_for_rag.txt"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GEE-RAG-Scraper/1.0; "
        "+https://github.com/your-project)"
    )
}

# ─────────────────────────────────────────────
# 1. 爬取官方单页 API 文档（最推荐，内容最全）
# ─────────────────────────────────────────────
def scrape_official_single_page() -> list[dict]:
    """
    https://developers.google.com/earth-engine/api_docs
    这一页包含所有 ee.* 函数，Markdown 表格形式列出参数和返回类型。
    """
    url = "https://developers.google.com/earth-engine/api_docs"
    print(f"[1/3] 爬取官方单页文档: {url}")
    
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    
    entries = []
    
    # 官方页面每个函数都是一个 <h2> 标题，后面跟 <table>
    # 标题格式如: ee.Image.normalizedDifference
    for h2 in soup.find_all("h2"):
        func_name = h2.get_text(strip=True)
        
        # 只取 ee.* 开头的
        if not func_name.startswith("ee."):
            continue
        
        # 取这个 h2 之后的第一个 table（包含 Usage、Returns、Args）
        sibling = h2.find_next_sibling()
        
        description = ""
        usage       = ""
        returns     = ""
        arguments   = []
        
        # 收集 h2 和下一个 h2 之间的文本（描述）
        desc_parts = []
        node = h2.next_sibling
        while node and node.name not in ("h2", "h3"):
            if hasattr(node, "get_text"):
                txt = node.get_text(separator=" ", strip=True)
                if txt and node.name not in ("table",):
                    desc_parts.append(txt)
            node = node.next_sibling
        description = " ".join(desc_parts).strip()
        
        # 解析 table
        tables = []
        node = h2.next_sibling
        while node:
            if node.name == "h2":
                break
            if node.name == "table":
                tables.append(node)
            node = node.next_sibling
        
        for table in tables:
            rows = table.find_all("tr")
            if not rows:
                continue
            
            header_cells = rows[0].find_all(["th", "td"])
            header_texts = [c.get_text(strip=True).lower() for c in header_cells]
            
            # Usage / Returns 表（第一行一般是 Usage | Returns）
            if "usage" in header_texts or "returns" in header_texts:
                for row in rows[1:]:
                    cells = row.find_all(["td"])
                    if len(cells) >= 2:
                        usage   = cells[0].get_text(strip=True)
                        returns = cells[1].get_text(strip=True)
                        break
            
            # Arguments 表（第一行是 Argument | Type | Details）
            if "argument" in header_texts or "type" in header_texts:
                for row in rows[1:]:
                    cells = row.find_all("td")
                    if len(cells) >= 3:
                        arg_name    = cells[0].get_text(strip=True)
                        arg_type    = cells[1].get_text(strip=True)
                        arg_details = cells[2].get_text(strip=True)
                        if arg_name:
                            arguments.append({
                                "name":    arg_name,
                                "type":    arg_type,
                                "details": arg_details,
                            })
        
        entry = {
            "source":      "official_api_docs",
            "name":        func_name,
            "description": description,
            "usage":       usage,
            "returns":     returns,
            "arguments":   arguments,
        }
        entries.append(entry)
    
    print(f"  ✓ 解析到 {len(entries)} 个 ee.* 函数")
    return entries


# ─────────────────────────────────────────────
# 2. 爬取 ReadTheDocs（Python 类方法层面的说明）
# ─────────────────────────────────────────────
READTHEDOCS_INDEX = [
    "https://gee-python-api.readthedocs.io/en/latest/ee.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.image.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.imagecollection.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.featurecollection.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.feature.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.geometry.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.filter.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.reducer.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.number.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.string.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.list.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.dictionary.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.date.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.array.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.kernel.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.terrain.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.classifier.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.clusterer.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.batch.html",
    "https://gee-python-api.readthedocs.io/en/latest/ee.data.html",
]

def scrape_readthedocs() -> list[dict]:
    """
    逐页爬取 ReadTheDocs 上的 ee.* 类/方法文档。
    """
    print(f"[2/3] 爬取 ReadTheDocs ({len(READTHEDOCS_INDEX)} 页) ...")
    entries = []
    
    for url in READTHEDOCS_INDEX:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 404:
                print(f"  跳过 (404): {url}")
                continue
            resp.raise_for_status()
        except Exception as e:
            print(f"  跳过 ({e}): {url}")
            continue
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # ReadTheDocs 用 dl.py 或 dl.method 标记每个函数/方法
        for dl in soup.find_all("dl"):
            dt = dl.find("dt")
            dd = dl.find("dd")
            if not dt:
                continue
            
            sig = dt.get_text(separator=" ", strip=True)
            desc = dd.get_text(separator=" ", strip=True) if dd else ""
            
            # 提取 class/module 路径，构造完整名字
            # e.g. "Image.normalizedDifference(bandNames=None)"
            # 从 URL 猜 module 前缀
            module_hint = url.split("/")[-1].replace(".html", "")  # e.g. "ee.image"
            
            entry = {
                "source":      "readthedocs",
                "name":        f"{module_hint} :: {sig[:120]}",  # 截断防止太长
                "description": desc[:1000],
                "usage":       sig,
                "returns":     "",
                "arguments":   [],
            }
            entries.append(entry)
        
        time.sleep(0.5)  # 礼貌性限流
    
    print(f"  ✓ 解析到 {len(entries)} 个方法条目")
    return entries


# ─────────────────────────────────────────────
# 3. 从本地安装的 earthengine-api 包提取 docstring
# ─────────────────────────────────────────────
def extract_from_local_package() -> list[dict]:
    """
    如果本地安装了 earthengine-api，用 inspect 提取 docstring。
    未安装时跳过，不影响整体流程。
    """
    print("[3/3] 尝试从本地 earthengine-api 包提取 docstring ...")
    entries = []
    
    try:
        import ee
        import inspect
    except ImportError:
        print("  ✗ earthengine-api 未安装，跳过本地提取")
        return entries
    
    # 需要先初始化才能动态加载全部 API 方法
    # 这里只做 offline 静态提取，不需要认证
    # 直接枚举模块里已有的类
    EE_CLASSES = [
        ee.Image, ee.ImageCollection, ee.FeatureCollection,
        ee.Feature, ee.Geometry, ee.Filter, ee.Reducer,
        ee.Number, ee.String, ee.List, ee.Dictionary,
        ee.Date, ee.Array, ee.Kernel, ee.Terrain,
        ee.Classifier, ee.Clusterer, ee.batch.Export,
    ]
    
    for cls in EE_CLASSES:
        cls_name = f"ee.{cls.__name__}"
        for method_name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if method_name.startswith("_"):
                continue
            
            try:
                sig  = str(inspect.signature(method))
                doc  = inspect.getdoc(method) or ""
            except Exception:
                continue
            
            full_name = f"{cls_name}.{method_name}"
            entry = {
                "source":      "local_package",
                "name":        full_name,
                "description": doc[:1000],
                "usage":       f"{full_name}{sig}",
                "returns":     "",
                "arguments":   [],
            }
            entries.append(entry)
    
    print(f"  ✓ 提取到 {len(entries)} 个方法")
    return entries


# ─────────────────────────────────────────────
# 4. 合并 + 去重 + 格式化为 RAG chunk
# ─────────────────────────────────────────────
def merge_and_deduplicate(all_entries: list[dict]) -> list[dict]:
    """按 name 去重，优先保留 official_api_docs 来源。"""
    seen     = {}
    priority = {"official_api_docs": 0, "local_package": 1, "readthedocs": 2}
    
    for e in all_entries:
        key = e["name"].strip()
        if key not in seen:
            seen[key] = e
        else:
            existing_prio = priority.get(seen[key]["source"], 99)
            new_prio      = priority.get(e["source"], 99)
            if new_prio < existing_prio:
                seen[key] = e
    
    return list(seen.values())


def entry_to_rag_chunk(entry: dict) -> str:
    """把一条函数文档转成适合 RAG 检索的纯文本 chunk。"""
    lines = [f"## {entry['name']}"]
    
    if entry.get("description"):
        lines.append(entry["description"])
    
    if entry.get("usage"):
        lines.append(f"**Usage:** `{entry['usage']}`")
    
    if entry.get("returns"):
        lines.append(f"**Returns:** {entry['returns']}")
    
    if entry.get("arguments"):
        lines.append("**Arguments:**")
        for arg in entry["arguments"]:
            arg_line = f"  - `{arg['name']}` ({arg['type']}): {arg['details']}"
            lines.append(arg_line)
    
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 5. 主流程
# ─────────────────────────────────────────────
def main():
    all_entries = []
    
    # 3路爬取
    try:
        all_entries += scrape_official_single_page()
    except Exception as e:
        print(f"  [!] 官方文档爬取失败: {e}")
    
    try:
        all_entries += scrape_readthedocs()
    except Exception as e:
        print(f"  [!] ReadTheDocs 爬取失败: {e}")
    
    try:
        all_entries += extract_from_local_package()
    except Exception as e:
        print(f"  [!] 本地包提取失败: {e}")
    
    print(f"\n合并前总条目: {len(all_entries)}")
    merged = merge_and_deduplicate(all_entries)
    print(f"去重后总条目: {len(merged)}")
    
    # 保存结构化 JSON
    with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 结构化数据已保存: {JSON_OUTPUT}")
    
    # 保存 RAG 文本（每个函数一个 chunk，用 ===分隔）
    with open(TEXT_OUTPUT, "w", encoding="utf-8") as f:
        for entry in merged:
            chunk = entry_to_rag_chunk(entry)
            f.write(chunk)
            f.write("\n\n===\n\n")  # chunk 分隔符
    print(f"✓ RAG 文本已保存: {TEXT_OUTPUT}")
    
    # 简单统计
    sources = {}
    for e in merged:
        s = e["source"]
        sources[s] = sources.get(s, 0) + 1
    print("\n数据来源分布:")
    for src, cnt in sources.items():
        print(f"  {src}: {cnt} 条")
    
    print("\n完成！接下来可以用 build_chroma_index.py 把这些 chunk 导入 Chroma。")


if __name__ == "__main__":
    main()
