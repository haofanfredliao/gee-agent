"""构建 Chroma 知识库：写入基础 GEE 文档片段。"""
import sys
from pathlib import Path

# 确保项目根在 path 中
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

# 硬编码的 GEE 文档片段（MVP 无需爬取）
DOCS = [
    "GEE 的 asset 是 Google Earth Engine 中的一种数据资源类型，可以是影像、影像集合、矢量等。"
    "用户可以将自己的数据上传为 asset，或使用 GEE 官方及社区公开的 asset。",
    "在 GEE 中加载官方数据集示例：使用 ee.Image('USGS/SRTMGL1_003') 可加载 SRTM 高程数据。"
    "在 Code Editor 中可用 .addToMap(image) 显示。",
    "NDVI（归一化植被指数）常用公式为 (NIR - Red) / (NIR + Red)。"
    "GEE 中可用 MODIS 产品 MOD13Q1 的 NDVI 波段，或 Landsat 的 NIR/Red 计算。",
    "MODIS/006/MOD13Q1 提供 250m 分辨率的 16 天合成 NDVI，适合大范围植被监测。"
    "过滤日期用 .filterDate(start, end)，过滤范围用 .filterBounds(geometry)。",
]


def main():
    from backend.app.services.chroma_store import add_documents
    add_documents(DOCS)
    print("Chroma 索引已写入，共", len(DOCS), "条文档。")


if __name__ == "__main__":
    main()
