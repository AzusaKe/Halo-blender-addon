param(
    [string]$Pack = 'F:\codex-cache\halo-blender-addon\tests\all-pack-roundtrip.zip',
    [string]$HaloRepository = 'F:\Halo',
    [string]$Cache = 'F:\codex-cache\halo-blender-addon\java-check'
)

$ErrorActionPreference = 'Stop'
$gradleHome = if ($env:GRADLE_USER_HOME) {
    [System.IO.Path]::GetFullPath($env:GRADLE_USER_HOME)
} else {
    Join-Path $env:USERPROFILE '.gradle'
}
$gradleModules = Join-Path $gradleHome 'caches\modules-2\files-2.1'
$loomMinecraft = Join-Path $gradleHome 'caches\fabric-loom\minecraftMaven\net\minecraft\minecraft-merged'
$source = Join-Path (Split-Path -Parent $PSScriptRoot) 'tests\java\JavaPackParseCheck.java'
New-Item -ItemType Directory -Force -Path $Cache | Out-Null

function First-Jar([string]$Root, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Root)) { return $null }
    return Get-ChildItem -LiteralPath $Root -Recurse -Filter $Name |
        Where-Object { $_.Name -notmatch '-(sources|javadoc|tests)\.jar$' } |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}

$dependencies = @(
    # Halo may update dependency patch versions independently of the add-on.
    # This parser smoke test only needs the newest compatible cached binary.
    (First-Jar "$gradleModules\com.google.code.gson\gson" 'gson-*.jar'),
    (First-Jar "$gradleModules\org.joml\joml" 'joml-*.jar'),
    (First-Jar $loomMinecraft '*1.20.1-net.fabricmc.yarn*jar'),
    (First-Jar "$gradleModules\org.slf4j\slf4j-api" 'slf4j-api-*.jar'),
    (First-Jar "$gradleModules\com.mojang\brigadier" 'brigadier-*.jar'),
    (First-Jar "$gradleModules\com.mojang\datafixerupper" 'datafixerupper-*.jar'),
    (First-Jar "$gradleModules\com.mojang\authlib" 'authlib-*.jar'),
    (First-Jar "$gradleModules\com.google.guava\guava" 'guava-*.jar'),
    (First-Jar "$gradleModules\it.unimi.dsi\fastutil" 'fastutil-*.jar')
)
if ($dependencies -contains $null) { throw 'Java parser check dependency jar is missing from the Gradle cache' }
$mainClasses = Join-Path $HaloRepository 'build\classes\java\main'
$coreClasses = Join-Path $HaloRepository 'core\build\classes\java\main'
$compileClasspath = (@($mainClasses, $coreClasses) + $dependencies) -join ';'
javac --release 17 -encoding UTF-8 -cp $compileClasspath -d $Cache $source
if ($LASTEXITCODE -ne 0) { throw 'Java parser check compilation failed' }
$runtimeClasspath = (@($Cache, $mainClasses, $coreClasses) + $dependencies) -join ';'
java -cp $runtimeClasspath JavaPackParseCheck $Pack
if ($LASTEXITCODE -ne 0) { throw 'Java parser rejected exported pack' }
