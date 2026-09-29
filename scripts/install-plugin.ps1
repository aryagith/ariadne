$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
$python = Join-Path $workspace '.venv/Scripts/python.exe'
$profileRoot = [Environment]::GetFolderPath('UserProfile')
if (-not $profileRoot) { throw 'Windows user profile could not be resolved.' }
$pluginTarget = Join-Path $profileRoot 'plugins/autoapply'
if (Test-Path -LiteralPath $pluginTarget) { throw "Plugin already exists at $pluginTarget. Use the documented update flow; do not overwrite it." }
$creator = Join-Path $profileRoot '.codex/skills/.system/plugin-creator/scripts'
& $python (Join-Path $creator 'create_basic_plugin.py') autoapply --path (Join-Path $profileRoot 'plugins') --with-skills --with-scripts --with-marketplace
if ($LASTEXITCODE -ne 0) { throw 'Personal marketplace registration failed.' }
Copy-Item -LiteralPath (Join-Path $workspace 'plugin/autoapply/.codex-plugin/plugin.json') -Destination (Join-Path $pluginTarget '.codex-plugin/plugin.json')
Copy-Item -LiteralPath (Join-Path $workspace 'plugin/autoapply/skills/autoapply') -Destination (Join-Path $pluginTarget 'skills') -Recurse
Copy-Item -LiteralPath (Join-Path $workspace 'plugin/autoapply/scripts/autoapply.ps1') -Destination (Join-Path $pluginTarget 'scripts/autoapply.ps1')
& $python (Join-Path $creator 'validate_plugin.py') $pluginTarget
if ($LASTEXITCODE -ne 0) { throw 'Installed source validation failed.' }
$marketplaceName = & $python (Join-Path $creator 'read_marketplace_name.py')
if ($LASTEXITCODE -ne 0) { throw 'Marketplace identifier validation failed.' }
& codex plugin add "autoapply@$marketplaceName"
if ($LASTEXITCODE -ne 0) { throw 'Plugin is registered, but automatic installation failed. Open it in Plugins to install.' }
Write-Host "Installed autoapply from $marketplaceName. Start a new conversation to pick up its skill."
