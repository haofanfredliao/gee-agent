"""沙箱执行规则：危险 pattern 列表与 GEE 约束文本块。

SANDBOX_UNSAFE_PATTERNS  — 在 exec() 之前对生成代码进行静态扫描，
                           命中任意一条则拒绝执行并返回 error。
SANDBOX_CONSTRAINTS_BLOCK — 注入 CODE_GEN_PROMPT / CODE_REPAIR_PROMPT 的
                           统一约束说明，所有 prompt 引用同一来源，避免重复。
"""
import re
from typing import List

# ---------------------------------------------------------------------------
# 危险代码模式：命中任意一条则拒绝执行
# ---------------------------------------------------------------------------
SANDBOX_UNSAFE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bimport\s+os\b"),
    re.compile(r"\bimport\s+subprocess\b"),
    re.compile(r"\bimport\s+shutil\b"),
    re.compile(r"\bimport\s+pathlib\b"),
    re.compile(r"\bimport\s+sys\b"),
    re.compile(r"\bimport\s+socket\b"),
    re.compile(r"\bimport\s+urllib\b"),
    re.compile(r"\bimport\s+requests\b"),
    re.compile(r"\bimport\s+httpx\b"),
    re.compile(r"\b__import__\s*\("),
    re.compile(r"\bopen\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bcompile\s*\("),
    re.compile(r"\bos\."),
    re.compile(r"\bsubprocess\."),
    re.compile(r"\bpathlib\."),
]

# ---------------------------------------------------------------------------
# GEE 约束文本块：CODE_GEN_PROMPT / CODE_REPAIR_PROMPT 共用
# ---------------------------------------------------------------------------
SANDBOX_CONSTRAINTS_BLOCK = """\
【沙箱约束 — 严格遵守，违反将导致执行错误】
1. 禁止使用 geemap，禁止写 import geemap。
2. 可视化图层时使用预注入的 Map 对象：
     Map.addLayer(ee_object, vis_params_dict, "图层名称")
   Map 已存在于执行环境，不要重新实例化，不要调用 Map.centerObject() 或 Map.setCenter()。
   - 若要可视化 ee.Image，直接传入 Image 对象和 vis_params。
   - 若要可视化 ee.FeatureCollection 或 ee.Feature，也直接传给 Map.addLayer，沙箱会自动将其转换为可视图层（无需手动 paint）。
3. 根据上下文中提供的实际字段名编写代码，禁止猜测或硬编码字段名。
4. 所有需要展示的结果用 print(...) 输出。
5. 不要调用 ee.Initialize() 或 ee.Authenticate()。
6. 若需要计算面积，使用 .area() 方法并指定单位（如 .divide(1e6) 转换为平方公里）。
7. 禁止调用 .style() 方法（Python earthengine-api 不支持此方法）。
8. 禁止在循环中调用 .getInfo()，应改用 reduceRegions 或 reduceToVectors 进行批量计算。
9. 使用 stratifiedSample 时必须指定 scale 参数（建议 30 或更大），不需要几何信息时设 geometries=False。
10. 统计影像在矢量区域内的唯一像素值数量（如每个区的土地利用类别数）：
    正确模式是在 FeatureCollection.map() 内对单个 feature 调用 reduceRegion（非 reduceRegions），使用 ee.Reducer.countDistinct()：
      def add_diversity(f):
          result = image.reduceRegion(reducer=ee.Reducer.countDistinct(), geometry=f.geometry(), scale=30, maxPixels=1e10)
          return f.set('diversity', ee.Number(result.get('b1')))
      fc_with_diversity = fc.map(add_diversity)
    countDistinct() 与单 feature 的 reduceRegion 配合使用是正确的；与 reduceRegions（批量）配合或与 frequencyHistogram 混用时可能返回 0。
11. 对 FeatureCollection 进行多键排序（主键 + 次键 tie-breaker）时，必须按"次键在前、主键在后"的顺序链式调用 .sort()：
    因为 GEE 的 .sort() 是稳定排序，最后调用的 sort 决定最终主排序键。
    正确示例（主键=多样性，次键=面积）：
      sorted_fc = fc.sort('area', False).sort('diversity', False)  # 先排面积，再排多样性
    错误示例（顺序颠倒，将以面积为主键）：
      sorted_fc = fc.sort('diversity', False).sort('area', False)  # 错误！最终主键变成了面积
12. 生成 GEE 分析代码时，scale 参数应与影像原始分辨率保持一致（如原始影像为 10m 则用 scale=10），
    不要随意降低为 30 或使用 bestEffort=True 来规避像素超限——应先通过 maxPixels=1e10 或更大值解决超限问题。
13. Sentinel-2 Surface Reflectance 数据集必须使用 "COPERNICUS/S2_SR_HARMONIZED"，
    严禁使用已弃用的 "COPERNICUS/S2_SR"。
14. "COPERNICUS/S2_SR_HARMONIZED" 与 "COPERNICUS/S2_CLOUD_PROBABILITY" 都是 ee.ImageCollection，
    必须用 ee.ImageCollection(...) 加载，严禁写成 ee.FeatureCollection(...)。
15. .mosaic() / .median() / .qualityMosaic() 仅可用于 ee.ImageCollection，
    严禁对 ee.FeatureCollection 或 ee.Feature 调用这些方法。
16. 对“全年 Sentinel-2 最少云 mosaic”任务，优先内存安全策略：
    禁止对全年集合做按天全量 map + 几何覆盖率遍历；
    必须先按 CLOUDY_PIXEL_PERCENTAGE 排序并 limit 候选集（如 40~100）后再做当天 mosaic 或小集合合成。
17. 用户请求某个 AOI/城市/行政区的遥感影像时，若上下文提供了 AOI 边界缓存，必须优先调用预注入 helper `load_aoi_boundary()`：
      aoi_fc = load_aoi_boundary()
      aoi = aoi_fc.geometry()
    不能在生成代码中联网请求 OSM/Nominatim/Overpass API；OSM 查询与缓存只能由后端完成。
    最终显示裁剪优先使用 image.clipToCollection(aoi_fc)；BBox 只能作为边界解析失败时的临时 fallback，不能冒充详细边界。
18. 只有用户明确要求 NDVI/NDBI/NDWI/MNDWI/NDMI/NBR/EVI/SAVI/BSI/LAI 等光谱指数时，
    才允许计算或显示指数图层；普通真彩色 mosaic 任务严禁额外添加指数图层。
19. 指数图层可视化必须使用指数专用范围与调色板（例如 NDVI min=-0.2 max=0.8；NDBI min=-0.5 max=0.5），
    严禁使用真彩色波段可视化参数（如 B4/B3/B2, min=0 max=3000 或 Landsat RGB min=0 max=0.3）渲染指数。
20. Sentinel-2 mosaic 用于 Map.addLayer 显示时，严禁调用 .reproject() 或 .setDefaultProjection() 强制投影；
    直接把 ee.Image 传给 Map.addLayer，让 Earth Engine 按地图瓦片投影渲染。
21. Sentinel-2 香港真彩色产品必须按用户措辞选择算法：
    - 明确说“同一天/当天/单日/云量最低那一天”时，生成同一天最低云 scene 集合的 mosaic。
    - 只说“最少云/少云/无云/尽量无云/cloudless/least cloud”且未要求同一天时，生成 SCL 云掩膜后的多景 median composite。
22. AOI 边界优先级：
    1) 用户明确提供的 GEE FeatureCollection asset；
    2) 后端已缓存的 OpenStreetMap 边界，通过 `load_aoi_boundary()` 读取；
    3) 数据集内置行政边界（如 USDOS/LSIB/2017）；
    4) BBox fallback。
    对香港旧代码可兼容 `osm_hk_boundary()`，但新代码应使用通用 `load_aoi_boundary()`。
    计算覆盖率/面积时可对 aoi.geometry() 使用 simplify(100) 后再 transform 到 EPSG:3857；
    最终影像 clip 尽量使用未简化的 aoi_fc，以保留海岸线/行政边界细节。
23. 选择遥感影像集合时必须按数据集类型使用正确的云量属性字段：
    - Sentinel-2 (COPERNICUS/S2_SR_HARMONIZED) → "CLOUDY_PIXEL_PERCENTAGE"
    - Landsat 8/9 (LANDSAT/LC08/C02/T1_L2, LANDSAT/LC09/C02/T1_L2) → "CLOUD_COVER"
    严禁在 Landsat 集合上 filter 或 sort "CLOUDY_PIXEL_PERCENTAGE"（字段不存在，会报错）。
24. Landsat Collection 2 Level 2 (SR) 影像在可视化前必须乘 scale_factor 还原为反射率：
    img.multiply(0.0000275).add(-0.2)
    RGB 波段名：["SR_B4", "SR_B3", "SR_B2"]，vis 范围建议 min=0.0, max=0.3。
    严禁对 Landsat SR 使用 ["B4","B3","B2"] 或 min=0/max=3000（Sentinel-2 参数）。
25. 计算 AOI 覆盖率 / 面积时，AOI 与影像 footprint 必须 transform 到同一米制投影（推荐 EPSG:3857），
    maxError 取 1（米）；禁止在 WGS84 下直接对不同 CRS 的 Geometry 做 intersection.area 相除。
26. "全年最少云 + 完整覆盖" 类任务的选片流水线（与规则 16 配合）:
    1) .sort(cloud_property).limit(CANDIDATE_LIMIT)（候选集上限 30，严禁 >= 50）；
    2) 仅在候选集的 distinct dates 上做 per-day 覆盖率聚合；
    3) 覆盖率 >= 99.9% 的日期里按 mean cloud 升序取第一；
    4) 若无日期达标，fallback 到覆盖率最高那天。
    严禁对原始全年集合直接做 per-date map 覆盖率遍历。
27. 计算 AOI 覆盖率 必须使用 Geometry 层面的运算（几何求交面积，毫秒级）:
    正确：
      covered = day_col.geometry().transform("EPSG:3857", 1) \\
                       .intersection(aoi_metric, 1).area(1)
      coverage_pct = ee.Number(covered).divide(aoi_area).multiply(100)
    严禁使用像素级运算（会触发 StaticPerfError）:
      image.mask().multiply(ee.Image.pixelArea()).reduceRegion(...)   # 禁止！
      mosaic.select(b).mask().reduceRegion(reducer=ee.Reducer.sum(), ...)  # 禁止！
    原因：像素级 reduceRegion 对每个候选日期都要遍历全 AOI 的像素（HK 约 1e8 个 10m 像素），
    30 个日期 = 3e9 次像素运算 → 必然 StaticPerfError。
28. 使用 ee.Algorithms.If 做 fallback 时，两个分支都必须显式返回非 null Feature：
    最外层应再包裹一层：ee.Algorithms.If(fc.size().gt(0), <正常分支>, ee.Feature(None, <含默认字段的 dict>))。
    避免 fallback 链中 .first() 在空 FeatureCollection 上返回 null，
    导致后续 .get("date") 抛出 "Element.get: Parameter 'object' is required and may not be null"。
    注意：本规则文本中禁止直接出现未转义的 Python dict 字面量（如单层花括号），
    因为 SANDBOX_CONSTRAINTS_BLOCK 会被拼入 prompt 模板并走 str.format()，会把花括号误当占位符。
29. 对 NDVI、mosaic、真彩色影像、遥感影像可视化请求，必须调用 Map.addLayer(...) 添加最终 ee.Image 图层。
    如果只 print 结果而没有 Map.addLayer，任务不算完成；NDVI 图层必须使用 NDVI 可视化参数。
"""
