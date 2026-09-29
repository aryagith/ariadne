$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
$python = Join-Path $workspace '.venv/Scripts/python.exe'
$profileRoot = [Environment]::GetFolderPath('UserProfile')
if (-not $profileRoot) { throw 'Windows user profile could not be resolved.' }
$creator = Join-Path $profileRoot '.codex/skills/.system/plugin-creator/scripts'
$marketplacePath = Join-Path $profileRoot '.agents/plugins/marketplace.json'
$marketplaceName = & $python (Join-Path $creator 'read_marketplace_name.py') --marketplace-path $marketplacePath
if ($LASTEXITCODE -ne 0) { throw 'Marketplace validation failed.' }
$catalog = Get-Content -LiteralPath $marketplacePath -Raw | ConvertFrom-Json
$entry = @($catalog.plugins | Where-Object { $_.name -eq 'autoapply' })
if ($entry.Count -ne 1 -or $entry[0].source.source -ne 'local' -or $entry[0].source.path -ne './plugins/autoapply') { throw 'Marketplace source differs from the expected personal Autoapply plugin.' }
$target = Join-Path $profileRoot 'plugins/autoapply'
if (-not (Test-Path -LiteralPath (Join-Path $target '.codex-plugin/plugin.json'))) { throw 'Installed plugin source is missing.' }
& $python (Join-Path $creator 'validate_plugin.py') (Join-Path $workspace 'plugin/autoapply')
if ($LASTEXITCODE -ne 0) { throw 'Staged plugin validation failed.' }
$manifestPath = Join-Path $target '.codex-plugin/plugin.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.name -ne 'autoapply') { throw 'Unexpected plugin name.' }
# Sync skill references too, so newly extracted guidance is included in the installed skill.
Copy-Item -LiteralPath (Join-Path $workspace 'plugin/autoapply/skills/autoapply/SKILL.md') -Destination (Join-Path $target 'skills/autoapply/SKILL.md')
$referenceTarget = Join-Path $target 'skills/autoapply/references'
New-Item -ItemType Directory -Path $referenceTarget -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $workspace 'plugin/autoapply/skills/autoapply/references') -Filter '*.md' -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $referenceTarget $_.Name)
}
$staged = Get-Content -LiteralPath (Join-Path $workspace 'plugin/autoapply/.codex-plugin/plugin.json') -Raw | ConvertFrom-Json
$manifest.interface = $staged.interface
$manifest.description = $staged.description
$manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $manifestPath -Encoding utf8
& $python (Join-Path $creator 'update_plugin_cachebuster.py') $target
if ($LASTEXITCODE -ne 0) { throw 'Plugin version update failed.' }
& $python (Join-Path $creator 'validate_plugin.py') $target
if ($LASTEXITCODE -ne 0) { throw 'Updated plugin validation failed.' }
& codex plugin add "autoapply@$marketplaceName"
if ($LASTEXITCODE -ne 0) { throw 'Reinstall failed; updated source remains available.' }
