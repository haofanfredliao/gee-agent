# 无 --reload 启动 FastAPI，避免热重载窗口内 /chat/stream 出现 502。

# 用法：在仓库根目录执行  powershell -File scripts/run_backend_stable.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

Set-Location $root

$env:PYTHONPATH = "."

Write-Host "Starting backend at http://127.0.0.1:8000 (no --reload). Ctrl+C to stop." -ForegroundColor Green

python -m uvicorn backend.app.main:app --port 8000

