param(
    [string]$Pack = 'F:\codex-cache\halo-blender-addon\tests\all-pack-roundtrip.zip',
    [string]$HaloRepository = 'F:\Halo',
    [string]$Cache = 'F:\codex-cache\halo-blender-addon\java-check'
)

$ErrorActionPreference = 'Stop'
$gradleModules = 'C:\Users\Azusa_Ke\.gradle\caches\modules-2\files-2.1'
$loomMinecraft = 'C:\Users\Azusa_Ke\.gradle\caches\fabric-loom\minecraftMaven\net\minecraft\minecraft-merged'
$source = Join-Path (Split-Path -Parent $PSScriptRoot) 'tests\java\JavaPackParseCheck.java'
New-Item -ItemType Directory -Force -Path $Cache | Out-Null

function First-Jar([string]$Root, [string]$Name) {
    return Get-ChildItem -LiteralPath $Root -Recurse -Filter $Name | Select-Object -First 1 -ExpandProperty FullName
}

$dependencies = @(
    (First-Jar "$gradleModules\com.google.code.gson\gson\2.10.1" 'gson-2.10.1.jar'),
    (First-Jar "$gradleModules\org.joml\joml\1.10.8" 'joml-1.10.8.jar'),
    (First-Jar $loomMinecraft '*1.20.1-net.fabricmc.yarn*jar'),
    (First-Jar "$gradleModules\org.slf4j\slf4j-api\2.0.9" 'slf4j-api-2.0.9.jar'),
    (First-Jar "$gradleModules\com.mojang\brigadier\1.1.8" 'brigadier-1.1.8.jar'),
    (First-Jar "$gradleModules\com.mojang\datafixerupper\6.0.8" 'datafixerupper-6.0.8.jar'),
    (First-Jar "$gradleModules\com.mojang\authlib\4.0.43" 'authlib-4.0.43.jar'),
    (First-Jar "$gradleModules\com.google.guava\guava" 'guava-31.1-jre.jar'),
    (First-Jar "$gradleModules\it.unimi.dsi\fastutil\8.5.12" 'fastutil-8.5.12.jar')
)
$mainClasses = Join-Path $HaloRepository 'build\classes\java\main'
$compileClasspath = (@($mainClasses) + $dependencies) -join ';'
javac --release 17 -encoding UTF-8 -cp $compileClasspath -d $Cache $source
if ($LASTEXITCODE -ne 0) { throw 'Java parser check compilation failed' }
$runtimeClasspath = (@($Cache, $mainClasses) + $dependencies) -join ';'
java -cp $runtimeClasspath JavaPackParseCheck $Pack
if ($LASTEXITCODE -ne 0) { throw 'Java parser rejected exported pack' }
