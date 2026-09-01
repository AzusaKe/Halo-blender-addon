param(
    [string]$BlenderExe = 'F:\codex-cache\halo-blender-addon\blender-5.2.0-windows-x64\blender.exe'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$extensionRoot = Join-Path $projectRoot 'halo_pack_editor'
$distRoot = Join-Path $projectRoot 'dist'
$packagePath = Join-Path $distRoot 'halo_pack_editor-0.3.5.zip'

if (-not (Test-Path -LiteralPath $BlenderExe)) {
    throw "Blender 5.2 executable not found: $BlenderExe"
}

# Do not load the user's installed extension into source-based tests, or
# create working caches in their normal Blender profile.
$buildProfile = Join-Path 'F:\codex-cache\halo-blender-addon' ('build-profile-' + [guid]::NewGuid().ToString('N'))
$previousEnvironment = @{}
foreach ($key in @('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_EXTENSIONS', 'BLENDER_USER_DATAFILES')) {
    $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    $directory = Join-Path $buildProfile $key
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    [Environment]::SetEnvironmentVariable($key, $directory, 'Process')
}
try {
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

$mergeTest = Join-Path $PSScriptRoot 'blender_merge_test.py'
if (Test-Path -LiteralPath $mergeTest) {
    & $BlenderExe --background --factory-startup --python-exit-code 1 --python $mergeTest -- $projectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Blender merged-project test failed' }
}

$reopenTest = Join-Path $PSScriptRoot 'blender_reopen_test.py'
$namespaceTest = Join-Path $PSScriptRoot 'blender_namespace_migration_test.py'
& $BlenderExe --background --factory-startup --python-exit-code 1 --python $namespaceTest -- $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Blender namespace texture migration test failed' }

$exportTextureTest = Join-Path $PSScriptRoot 'blender_export_texture_test.py'
& $BlenderExe --background --factory-startup --python-exit-code 1 --python $exportTextureTest -- $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Blender export texture pruning test failed' }

$resourceTest = Join-Path $PSScriptRoot 'blender_resource_persistence_test.py'
$resourceTestRoot = Join-Path 'F:\codex-cache\halo-blender-addon' ('resources-' + [guid]::NewGuid().ToString('N'))
foreach ($testMode in @('prepare', 'reopen', 'legacy_prepare', 'legacy_reopen', 'cleanup_prepare', 'cleanup_reopen')) {
    & $BlenderExe --background --factory-startup --python-exit-code 1 --python $resourceTest -- $projectRoot $resourceTestRoot $testMode
    if ($LASTEXITCODE -ne 0) { throw "Blender resource persistence test failed: $testMode" }
}

$blendFile = 'F:\codex-cache\halo-blender-addon\tests\hina-editor.blend'
if ((Test-Path -LiteralPath $reopenTest) -and (Test-Path -LiteralPath $blendFile)) {
    & $BlenderExe --background $blendFile --python-exit-code 1 --python $reopenTest -- $projectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Blender save/reopen test failed' }
}

$meshReopenTest = Join-Path $PSScriptRoot 'blender_mesh_reopen_test.py'
$meshBlendFile = 'F:\codex-cache\halo-blender-addon\tests\mesh-conversion.blend'
if ((Test-Path -LiteralPath $meshReopenTest) -and (Test-Path -LiteralPath $meshBlendFile)) {
    & $BlenderExe --background $meshBlendFile --python-exit-code 1 --python $meshReopenTest -- $projectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Blender Mesh conversion save/reopen test failed' }
}

$renderTest = Join-Path $PSScriptRoot 'blender_render_test.py'
if (Test-Path -LiteralPath $renderTest) {
    & $BlenderExe --background --factory-startup --python-exit-code 1 --python $renderTest -- $projectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Blender EEVEE/Cycles render test failed' }
}

Write-Host "Built and validated: $packagePath"
} finally {
    foreach ($key in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previousEnvironment[$key], 'Process')
    }
}
