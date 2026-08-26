param(
    [string]$BlenderExe = 'F:\codex-cache\halo-blender-addon\blender-5.2.0-windows-x64\blender.exe'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$extensionRoot = Join-Path $projectRoot 'halo_pack_editor'
$distRoot = Join-Path $projectRoot 'dist'
$packagePath = Join-Path $distRoot 'halo_pack_editor-0.1.3.zip'

if (-not (Test-Path -LiteralPath $BlenderExe)) {
    throw "Blender 5.2 executable not found: $BlenderExe"
}

python -m unittest discover -s (Join-Path $projectRoot 'tests') -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Pure Python tests failed' }

& $BlenderExe --command extension validate $extensionRoot
if ($LASTEXITCODE -ne 0) { throw 'Extension source validation failed' }

& $BlenderExe --command extension build `
    --source-dir $extensionRoot `
    --output-filepath $packagePath
if ($LASTEXITCODE -ne 0) { throw 'Extension build failed' }

& $BlenderExe --command extension validate $packagePath
if ($LASTEXITCODE -ne 0) { throw 'Built package validation failed' }

$smokeTest = Join-Path $PSScriptRoot 'blender_smoke_test.py'
if (Test-Path -LiteralPath $smokeTest) {
    & $BlenderExe --background --factory-startup --python-exit-code 1 --python $smokeTest -- $projectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Blender background smoke test failed' }
}

$reopenTest = Join-Path $PSScriptRoot 'blender_reopen_test.py'
$blendFile = 'F:\codex-cache\halo-blender-addon\tests\hina-editor.blend'
if ((Test-Path -LiteralPath $reopenTest) -and (Test-Path -LiteralPath $blendFile)) {
    & $BlenderExe --background $blendFile --python-exit-code 1 --python $reopenTest -- $projectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Blender save/reopen test failed' }
}

Write-Host "Built and validated: $packagePath"
