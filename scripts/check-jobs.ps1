$ErrorActionPreference = 'Stop'
$taskWorkspace = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $taskWorkspace
try {
    & (Join-Path $taskWorkspace '.venv/Scripts/python.exe') -m backend.job_watch
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
