"""验证地理编码：传入地名（如 Hong Kong）返回经纬度与 bbox。"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))


def main():
    from backend.app.services.geocoding import geocode_place_name
    name = "Hong Kong"
    if len(sys.argv) > 1:
        name = " ".join(sys.argv[1:])
    lat, lon, bbox = geocode_place_name(name)
    print(f"地名: {name}")
    print(f"中心: ({lat}, {lon})")
    print(f"bbox: {bbox}")


if __name__ == "__main__":
    main()
