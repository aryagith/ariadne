param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ToolArguments
)
$ErrorActionPreference = 'Stop'
$workspace = 'C:\projects\autoapply'
$python = Join-Path $workspace '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Autoapply runtime is missing. See C:\projects\autoapply\README.md.' }
Push-Location -LiteralPath $workspace
try {
    & $python -m backend.tool @ToolArguments
    exit $LASTEXITCODE
} finally { Pop-Location }
