"""沙箱 API：POST /sandbox/run —— 在受控环境中执行 GEE Python 代码。"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.tools.execution.gee_executor import execute_gee_snippet

router = APIRouter()


class SandboxRunRequest(BaseModel):
    code: str


@router.post("/run")
async def sandbox_run(request: SandboxRunRequest):
    """
    在沙箱中执行 GEE Python 代码。

    代码可直接使用 `ee` 和 `Map` 两个预置变量（无需 ee.Initialize）。
    Map.addLayer 调用结果将以 tile_url / layers 形式返回。

    Returns
    -------
    {
        "status"  : "ok" | "error",
        "log"     : str,
        "tile_url": Optional[str],
        "layers"  : List[{"name": str, "tile_url": str}]
    }
    """
    result = execute_gee_snippet(request.code)
    return result
