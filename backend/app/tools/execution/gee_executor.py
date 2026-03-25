"""GEE 代码执行工具（execution 层）。

在预置的 GEE Python 环境中执行代码片段，捕获 stdout 和地图图层。
这是 orchestrator execute 步骤的核心执行器。

安全说明
--------
- exec() 仅在受控的 global_env 中运行，隔离于应用全局命名空间。
- 执行环境只预注入 `ee`（已初始化的 earthengine-api）和 `Map`（MockMap 对象）。
- 用户代码不能访问文件系统、网络或其他系统资源（earthengine-api 本身会通过
  OAuth2 访问 GEE 服务，但这是预期行为）。
"""
import io
import sys
from typing import Any, Dict, List, Optional


def execute_gee_snippet(code: str) -> Dict[str, Any]:
    """
    在预置 GEE 环境中执行 Python 代码片段。

    执行环境内置：
      - `ee`  : 已初始化的 earthengine-api 模块
      - `Map` : MockMap，捕获 addLayer 调用以提取 tile URL

    Parameters
    ----------
    code : str
        合法的 GEE Python 代码（不含 ee.Initialize / ee.Authenticate）。

    Returns
    -------
    dict with keys:
        status    : "ok" | "error"
        log       : str   — 捕获的 stdout 输出
        tile_url  : Optional[str]  — 最后一次 addLayer 的 tile URL
        layers    : List[dict]     — 所有 addLayer 调用的图层信息列表
    """
    from backend.app.services.gee_client import init_gee_client

    if not init_gee_client():
        return {"status": "error", "log": "GEE 未初始化", "tile_url": None, "layers": []}

    try:
        import ee
    except ImportError:
        return {"status": "error", "log": "earthengine-api 未安装", "tile_url": None, "layers": []}

    class _MockMap:
        """拦截 Map.addLayer，收集 tile URL，同时支持 centerObject/setCenter 的空实现。"""

        def __init__(self) -> None:
            self.tile_url: Optional[str] = None
            self.layers: List[Dict[str, Any]] = []

        def addLayer(  # noqa: N802 — 保持与 GEE/geemap 接口一致的命名
            self,
            ee_object: Any,
            vis_params: Optional[Dict] = None,
            name: Optional[str] = None,
            shown: bool = True,
            opacity: float = 1.0,
        ) -> None:
            try:
                map_id = ee_object.getMapId(vis_params or {})
                url = map_id.get("tile_fetcher").url_format if map_id else None
                if url:
                    self.tile_url = url
                    self.layers.append({"name": name or "layer", "tile_url": url})
            except Exception as err:
                print(f"[Map.addLayer error] {err}")

        def centerObject(self, ee_object: Any, zoom: Optional[int] = None) -> None:  # noqa: N802
            pass

        def setCenter(self, lon: float, lat: float, zoom: Optional[int] = None) -> None:  # noqa: N802
            pass

    mock_map = _MockMap()
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured

    global_env: Dict[str, Any] = {"ee": ee, "Map": mock_map}

    try:
        exec(code, global_env, global_env)  # nosec B102
        return {
            "status": "ok",
            "log": captured.getvalue(),
            "tile_url": mock_map.tile_url,
            "layers": mock_map.layers,
        }
    except Exception as err:
        return {
            "status": "error",
            "log": captured.getvalue() + f"\nError: {err}",
            "tile_url": None,
            "layers": [],
        }
    finally:
        sys.stdout = old_stdout
