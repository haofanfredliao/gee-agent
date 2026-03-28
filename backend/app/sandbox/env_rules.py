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
"""
