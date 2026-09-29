param([string]$Python = 'python', [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv/Scripts/python.exe')) { & $Python -m venv .venv }
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency install failed' }
Push-Location web
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Node dependency install failed' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard build failed' }
} finally { Pop-Location }
$env:APP_ORIGIN = "http://127.0.0.1:$Port"
Write-Host "Dashboard: $env:APP_ORIGIN. Passphrase: data/auth-token.txt (or configured APP_PASSWORD)."
& ./.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
