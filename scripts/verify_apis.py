#!/usr/bin/env python3
"""
验证 .env 中的 Google Geocoding API 和 Poe API 是否可用。
使用前请将 .env.example 复制为 .env 并填入真实 key。
在项目虚拟环境中运行：pip install -e . 后再执行本脚本。
"""
import asyncio
import os
import sys
from pathlib import Path

# 项目根加入 path，并优先加载 .env
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

# 加载 .env；若无则尝试 .env.example（仅便于验证，正式使用请用 .env 并勿提交密钥）
env_file = root / ".env"
if not env_file.exists():
    env_file = root / ".env.example"
    if env_file.exists():
        print("提示：使用 .env.example 中的变量。正式环境请复制为 .env 并勿将密钥提交到 git。\n")
if env_file.exists():
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                if v and v != "...":  # 跳过占位值
                    os.environ[k.strip()] = v


def check_geocoding() -> bool:
    """验证 Google Geocoding API：用非内置地名（东京）请求，看是否返回合理坐标。"""
    from backend.app.services.geocoding import geocode_place_name

    if not os.environ.get("GEOCODING_API_KEY"):
        print("[GEOCODING] 未设置 GEOCODING_API_KEY，跳过。")
        return False
    print("[GEOCODING] 正在请求「东京」...")
    try:
        lat, lon, bbox = geocode_place_name("东京")
        # 东京大约 35.6°N, 139.7°E
        if 35.0 < lat < 36.5 and 139.0 < lon < 140.5:
            print(f"[GEOCODING] 通过。中心: ({lat}, {lon}), bbox: {bbox}")
            return True
        print(f"[GEOCODING] 返回坐标不像东京，请检查 API/配额。中心: ({lat}, {lon})")
        return False
    except Exception as e:
        print(f"[GEOCODING] 失败: {e}")
        return False


async def check_poe() -> bool:
    """验证 Poe API：发一句短问，检查是否拿到非占位回复。"""
    from backend.app.services.llm_client import chat_with_llm

    if not os.environ.get("POE_API_KEY"):
        print("[POE] 未设置 POE_API_KEY，跳过。")
        return False
    print("[POE] 正在发送测试问题...")
    try:
        reply = await chat_with_llm("请只回复一个字：好")
        if not reply:
            print("[POE] 返回为空。")
            return False
        if "[占位]" in reply or "[Poe API 错误]" in reply or "[Poe API 异常]" in reply:
            print(f"[POE] 未通过（占位或错误）: {reply[:200]}")
            return False
        print(f"[POE] 通过。回复预览: {reply[:100]}...")
        return True
    except Exception as e:
        print(f"[POE] 失败: {e}")
        return False


def main():
    print("=== API 可用性检查 ===\n")
    try:
        geo_ok = check_geocoding()
    except Exception as e:
        print(f"[GEOCODING] 导入或执行出错: {e}")
        print("  请先在虚拟环境中安装依赖: pip install -e .\n")
        geo_ok = False
    print()
    try:
        poe_ok = asyncio.run(check_poe())
    except Exception as e:
        print(f"[POE] 导入或执行出错: {e}")
        print("  请先在虚拟环境中安装依赖: pip install -e .\n")
        poe_ok = False
    print()
    if geo_ok and poe_ok:
        print("全部通过。")
    elif geo_ok or poe_ok:
        print("部分通过，请检查未通过项的 key 与配额。")
    else:
        print("均未通过，请确认 .env 中 GEOCODING_API_KEY / POE_API_KEY 正确且未超配额。")
    return 0 if (geo_ok and poe_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
