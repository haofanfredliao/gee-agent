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
3. 根据上下文中提供的实际字段名编写代码，禁止猜测或硬编码字段名。
4. 所有需要展示的结果用 print(...) 输出。
5. 不要调用 ee.Initialize() 或 ee.Authenticate()。
6. 若需要计算面积，使用 .area() 方法并指定单位（如 .divide(1e6) 转换为平方公里）。
7. 禁止调用 .style() 方法（Python earthengine-api 不支持此方法）。
8. 禁止在循环中调用 .getInfo()，应改用 reduceRegions 或 reduceToVectors 进行批量计算。
9. 使用 stratifiedSample 时必须指定 scale 参数（建议 30 或更大），不需要几何信息时设 geometries=False。
"""
