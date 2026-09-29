$ErrorActionPreference = 'Stop'
$taskWorkspace = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $taskWorkspace
try {
    & (Join-Path $taskWorkspace '.venv/Scripts/python.exe') -m backend.tool sync-tracker
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
