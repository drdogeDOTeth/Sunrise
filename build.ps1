<#
.SYNOPSIS
    Builds steam_api64.dll from the Visual Studio solution.

.DESCRIPTION
    Sunrise.vcxproj pins PlatformToolset v145, which ships with Visual Studio 2026.
    On a VS 2022 install the newest toolset is v143 (MSVC 14.4x), so this script
    detects the installed toolset and overrides only when v145 is absent. The
    Windows SDK the project asks for (10.0.26100.0) is present on both.

.PARAMETER Configuration
    Release (default) or Debug.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Configuration Debug
#>
[CmdletBinding()]
param(
    [ValidateSet('Release', 'Debug')]
    [string]$Configuration = 'Release'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    throw "vswhere.exe not found. Install Visual Studio with the 'Desktop development with C++' workload."
}

$vsPath = & $vswhere -latest -prerelease -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format value -property installationPath
if (-not $vsPath) {
    throw "No Visual Studio install with the C++ toolset was found."
}

$msbuild = Join-Path $vsPath 'MSBuild\Current\Bin\MSBuild.exe'
if (-not (Test-Path $msbuild)) {
    throw "MSBuild.exe not found under $vsPath."
}

# v145 lives in MSBuild\Microsoft\VC as a versioned folder. Anything older tops out at v143.
$toolsetRoot = Join-Path $vsPath 'MSBuild\Microsoft\VC'
$hasV145 = Test-Path (Join-Path $toolsetRoot 'v170\Microsoft.Cpp.v145.props')

$msbuildArgs = @(
    (Join-Path $root 'Sunrise.sln')
    "/p:Configuration=$Configuration"
    '/p:Platform=x64'
    '/m'
    '/v:minimal'
)
if (-not $hasV145) {
    Write-Host "PlatformToolset v145 not installed; falling back to v143." -ForegroundColor Yellow
    $msbuildArgs += '/p:PlatformToolset=v143'
}

Write-Host "Building $Configuration with $vsPath" -ForegroundColor Cyan
& $msbuild @msbuildArgs
if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE."
}

$dll = Join-Path $root "build\x64\$Configuration\steam_api64.dll"
if (-not (Test-Path $dll)) {
    throw "Build reported success but $dll is missing."
}

$info = Get-Item $dll
Write-Host ""
Write-Host "Built: $($info.FullName)" -ForegroundColor Green
Write-Host "Size:  $([math]::Round($info.Length / 1MB, 2)) MB"
Write-Host ""
Write-Host "Install: copy over <game>\bin\x64\steam_api64.dll (keep a backup of the original)."
