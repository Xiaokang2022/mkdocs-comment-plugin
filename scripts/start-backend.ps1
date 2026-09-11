# 一键启动评论后端
# 用法: .\scripts\start-backend.ps1 [-Port 8000]

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"

Write-Host "==> 启动评论后端 (http://127.0.0.1:$Port)" -ForegroundColor Cyan
Write-Host "    接口文档: http://127.0.0.1:$Port/docs" -ForegroundColor DarkGray
Set-Location $backend

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "==> 已从 .env.example 生成 .env" -ForegroundColor Yellow
    }
}

python -m uvicorn main:app --host 0.0.0.0 --port $Port --reload
