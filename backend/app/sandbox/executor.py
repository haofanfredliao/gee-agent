"""沙箱执行器：pre-exec 安全检查 + MockMap + 受控 exec()。

对外只暴露 `run(code, gee_module)` 一个函数，屏蔽内部 exec() 细节。
"""
import io
import sys
from typing import Any, Dict, List, Optional

from backend.app.sandbox.env_rules import SANDBOX_UNSAFE_PATTERNS


def check_code_safety(code: str) -> Optional[str]:
    """
    静态扫描代码是否含有危险 pattern。

    Returns
    -------
    None  ：代码安全，可以执行。
    str   ：发现的第一条违规描述。
    """
    for pattern in SANDBOX_UNSAFE_PATTERNS:
        if pattern.search(code):
            return f"代码含有被禁止的操作：{pattern.pattern}"
    return None


class _MockMap:
    """
    拦截 Map.addLayer，收集 tile URL，同时提供 centerObject/setCenter 空实现。
    保持与 GEE/geemap 接口一致的方法命名（addLayer / centerObject / setCenter）。
    """

    def __init__(self) -> None:
        self.tile_url: Optional[str] = None
        self.layers: List[Dict[str, Any]] = []

    def addLayer(  # noqa: N802
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
        except AttributeError:
            # ee_object 不是 Image（如 FeatureCollection 或 Feature），用 paint 转换后再取 tile。
            try:
                import ee as _ee
                paint_vis = vis_params or {"palette": ["FF0000"]}
                try:
                    map_id = _ee.Image().paint(ee_object, 1).getMapId(paint_vis)
                except Exception:
                    # ee_object 是 Feature，需先包装成 FeatureCollection
                    map_id = _ee.Image().paint(
                        _ee.FeatureCollection([ee_object]), 1
                    ).getMapId(paint_vis)
                url = map_id.get("tile_fetcher").url_format if map_id else None
                if url:
                    self.tile_url = url
                    self.layers.append({"name": name or "layer", "tile_url": url})
            except Exception as err2:
                print(f"[Map.addLayer error] {err2}")
        except Exception as err:
            print(f"[Map.addLayer error] {err}")

    def centerObject(self, ee_object: Any, zoom: Optional[int] = None) -> None:  # noqa: N802
        pass

    def setCenter(self, lon: float, lat: float, zoom: Optional[int] = None) -> None:  # noqa: N802
        pass


def run(code: str, gee_module: Any) -> Dict[str, Any]:
    """
    在受控沙箱中执行 GEE 代码片段。

    执行步骤：
      1. pre-exec 静态安全检查（禁止模式扫描）
      2. 将代码注入仅含 ee + Map 的隔离命名空间
      3. 捕获 stdout｜收集图层信息

    Parameters
    ----------
    code       : LLM 生成的 GEE Python 代码（不含 ee.Initialize）
    gee_module : 已初始化的 earthengine-api（ee）模块

    Returns
    -------
    dict with keys:
        status   : "ok" | "error"
        log      : str        — 捕获的 stdout 输出
        tile_url : Optional[str]   — 最后一次 addLayer 的 tile URL
        layers   : List[dict] — 所有 addLayer 调用的图层信息
    """
    # 1. 安全检查：命中则拒绝执行
    violation = check_code_safety(code)
    if violation:
        return {
            "status": "error",
            "log": f"沙箱拒绝执行：{violation}",
            "tile_url": None,
            "layers": [],
        }

    mock_map = _MockMap()
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured

    # 2. 隔离命名空间：只预置 ee 和 Map
    global_env: Dict[str, Any] = {"ee": gee_module, "Map": mock_map}

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
