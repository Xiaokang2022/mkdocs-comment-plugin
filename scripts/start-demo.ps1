# 构建并本地预览演示站点
# 用法: .\scripts\start-demo.ps1 [-Port 8001]

param(
    [int]$Port = 8001
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$demo = Join-Path $root "demo"

Push-Location $root
Write-Host "==> 以可编辑模式安装插件" -ForegroundColor Cyan
python -m pip install -e . | Out-Null
Pop-Location

Set-Location $demo
Write-Host "==> 启动 MkDocs 预览 (http://127.0.0.1:$Port)" -ForegroundColor Cyan
Write-Host "    请确认后端已在 http://127.0.0.1:8000 运行" -ForegroundColor DarkGray
python -m mkdocs serve --dev-addr "127.0.0.1:$Port"
