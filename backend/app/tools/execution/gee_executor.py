"""GEE 代码执行工具（execution 层）。

在预置的 GEE Python 环境中执行代码片段，捕获 stdout 和地图图层。
这是 orchestrator execute 步骤的核心执行器。

安全说明
--------
- 执行前由 sandbox.executor.check_code_safety() 进行静态 pattern 扫描，
  命中危险模式（import os / subprocess / open() 等）则拒绝执行。
- exec() 仅在受控的 global_env 中运行，隔离于应用全局命名空间。
- 执行环境只预注入 `ee`（已初始化的 earthengine-api）和 `Map`（MockMap 对象），
  详见 sandbox/executor.py。
"""
from typing import Any, Dict, Optional


def execute_gee_snippet(code: str, *, aoi_boundary_path: Optional[str] = None) -> Dict[str, Any]:
    """
    在预置 GEE 环境中执行 Python 代码片段。

    执行环境内置：
      - `ee`  : 已初始化的 earthengine-api 模块
      - `Map` : MockMap，捕获 addLayer 调用以提取 tile URL

    Parameters
    ----------
    code : str
        合法的 GEE Python 代码（不含 ee.Initialize / ee.Authenticate）。
    aoi_boundary_path : Optional[str]
        Local GeoJSON cache path prepared by the orchestrator. When provided,
        generated code can call load_aoi_boundary() inside the sandbox.

    Returns
    -------
    dict with keys:
        status    : "ok" | "error"
        log       : str              — 捕获的 stdout 输出
        tile_url  : Optional[str]    — 最后一次 addLayer 的 tile URL
        layers    : List[dict]       — 所有 addLayer 调用的图层信息列表
    """
    from backend.app.services.gee_client import init_gee_client
    from backend.app.sandbox.executor import run as sandbox_run

    if not init_gee_client():
        return {"status": "error", "log": "GEE 未初始化", "tile_url": None, "layers": []}

    try:
        import ee
    except ImportError:
        return {"status": "error", "log": "earthengine-api 未安装", "tile_url": None, "layers": []}

    return sandbox_run(code, ee, aoi_boundary_path=aoi_boundary_path)
