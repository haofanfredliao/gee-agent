"""验证 GEE 是否能正常初始化并打印简单结果。运行前会加载 .env（或 .env.example）中的 GEE_PROJECT_ID。"""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

# 先加载 .env（或 .env.example），否则 backend 读不到 GEE_PROJECT_ID
for env_path in (root / ".env", root / ".env.example"):
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    v = v.strip().strip('"').strip("'")
                    if v and v != "...":
                        os.environ[k.strip()] = v
        break


def main():
    from backend.app.services.gee_client import init_gee_client, load_simple_asset
    ok = init_gee_client()
    if not ok:
        project = os.environ.get("GEE_PROJECT_ID", "")
        if not project:
            print("GEE 未初始化：未设置 GEE_PROJECT_ID。请在 .env 或 configs/settings.yaml 中配置。")
        else:
            print("GEE 未初始化（未安装 earthengine-api 或未认证）。将使用占位逻辑。")
        return
    print("GEE 初始化成功。")
    result = load_simple_asset("USGS/SRTMGL1_003")
    print("load_simple_asset 结果:", result)


if __name__ == "__main__":
    main()
