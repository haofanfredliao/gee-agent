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
    # 禁止已知错误/不可访问的香港边界幻觉资产路径
    re.compile(r"projects/google/ft_assets/2023-08-01/HK_District_Boundaries"),
    re.compile(r"projects/google/datasets/geo/boundaries/HK_WanChai"),
    # 常见无效日期写法：会触发 Invalid argument specified for ee.Date(): None
    re.compile(r"ee\.Date\(\s*None\s*\)"),
    # Python 语法冲突：GEE Filter 组合不能用小写 and/or/not 形式
    re.compile(r"ee\.Filter\.and\s*\("),
    re.compile(r"ee\.Filter\.or\s*\("),
    re.compile(r"ee\.Filter\.not\s*\("),
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
13. Python 输出统计值时，仅允许对“最终小结果对象”做物化（如 reduceRegion(...).getInfo()）。
    禁止在循环或中间步骤频繁物化；可以一次性物化最终字典，再在 Python 侧拆字段打印。
    禁止直接 print ee.Dictionary/ee.Number 造成 ComputedObject 占位输出。
14. 禁止使用占位符形式的资产路径（如 `projects/reference_map_asset_path`、`projects/sample_points_asset_path`）。
    若上下文缺少真实可读 asset ID，优先尝试同语义的文档化公共资产回退，并在输出中标注 `WARN_USING_FALLBACK_ASSET`。
15. 若用户或上下文已明确给定资产路径（特别是行政区边界资产），应优先复用已确认路径。
    若资产不可访问，允许切换到同语义的文档化替代资产继续执行，并在输出中标注 `WARN_ASSET_FALLBACK`。
16. 香港区级行政区划任务中，若用户已确认
    `projects/ee-hku-geog7310/assets/Hong_Kong_District_Boundary`，
    应优先使用该路径；若 inspect/load 失败，可回退到文档化替代边界（如 `FAO/GAUL/2015/level2`）
    或其它已验证路径，并在输出中说明边界来源变更。
    在筛选具体区名（如中西区）前，必须先打印区名预览并再匹配：
    - 先打印 `NAME_EN` / `NAME_TC` 的候选区名列表（建议前 20-30 个）；
    - 再基于别名做 exact + contains 的两阶段匹配；
    - 打印 `candidate_count`、`final_count`、`matched_names_preview`，再进入后续统计。
    - 优先使用“多层预检模板”（见第 19 条）并保持打印键名一致，便于跨任务复用。
17. 禁止使用 `ee.Date(None)` 作为“当前时间”占位写法。
    Python 环境中也禁止使用 `ee.Date(Date.now())`（`Date` 是 JavaScript 全局对象，Python 不可用）。
    需要“当前时间/最近窗口”时，优先从数据集时间戳推导（如 `aggregate_max('system:time_start')`），
    或使用明确的日期字符串 / `ee.Date.fromYMD(...)`。
18. 当用户明确指定区级/县级行政边界且 AOI 解析失败时，
    优先尝试同级别边界数据源回退；仍失败时可在用户未反对的前提下使用受控近似 AOI（如小范围 bbox）
    继续给出参考结果，并在输出中标注 `WARN_APPROX_AOI` 与不确定性说明。
19. 行政区名称匹配必须使用“可复用多层预检”：
    - L1 字段层：打印 `boundary_asset`、`property_names_preview`（至少包含名称字段）；
    - L2 候选层：打印 `name_en_preview`、`name_tc_preview`（前 20-30 条）；
    - L3 精确层：打印 `exact_match_count`、`exact_matched_names_preview`；
    - L4 模糊层：打印 `contains_match_count`、`contains_matched_names_preview`；
    - L5 最终层：打印 `final_count`、`final_matched_names_preview`、`aoi_source`。
    若 `final_count==0`，输出 `WARN_APPROX_AOI` 并进入降级模式；禁止跳过上述层级直接做统计。
20. 香港区级边界（`projects/ee-hku-geog7310/assets/Hong_Kong_District_Boundary`）必须先打印完整区名清单再筛选：
    - 必须先 inspect 字段名，并确认名称字段（优先 `NAME_TC`、`NAME_EN`）；
    - 必须打印完整 `district_names_tc_all` 与 `district_names_en_all`（可用 `aggregate_array(...).distinct().sort()`）；
    - 再做 exact + contains 两阶段匹配，且优先用官方全称匹配（例如 `Central and Western District`）。
    对“中西区/中西區”目标，至少包含以下别名：
    - 中文：`中西區`、`中西区`
    - 英文：`Central and Western District`、`Central and Western`
    对香港其余区名匹配，允许使用以下官方英文全称（必要时可去掉 `District` 后缀再做 contains）：
    - `Wan Chai District`, `Eastern District`, `Southern District`, `Yau Tsim Mong District`,
      `Sham Shui Po District`, `Kowloon City District`, `Wong Tai Sin District`, `Kwun Tong District`,
      `Kwai Tsing District`, `Tsuen Wan District`, `Tuen Mun District`, `Yuen Long District`,
      `North District`, `Tai Po District`, `Sha Tin District`, `Sai Kung District`, `Islands District`.
21. Python 语法安全（Filter 组合）：
    - 禁止使用 `ee.Filter.and(...)`、`ee.Filter.or(...)`、`ee.Filter.not(...)`（Python 中会触发语法问题或歧义）；
    - 必须使用 `ee.Filter.And(...)`、`ee.Filter.Or(...)`、`ee.Filter.Not(...)`，或改为链式 `.filter(...)`。
22. 涉及多波段影像统计前，必须先打印波段诊断信息，至少包含：
    - `band_names`（可用波段列表）
    - `selected_band`（最终用于统计的波段）
    先打印再进入 reduceRegion/reduceRegions，便于快速定位“波段名不匹配”问题。
23. 结果表中的空值必须保持为空（null/None），禁止用 0 填充。
    适用于 `mean_*`、`image_count` 等字段；仅当计算结果确实为数值 0 时才允许输出 0。
"""
