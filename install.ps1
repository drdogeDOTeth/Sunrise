<#
.SYNOPSIS
    Installs the built steam_api64.dll into a Destiny 2 / Sunrise game folder.

.DESCRIPTION
    Backs up the game's original steam_api64.dll once, then copies this fork's build over it.
    The first backup is the real one: it is taken only when no backup exists yet, so running this
    repeatedly can never overwrite the genuine Steam DLL with a Sunrise build.

.PARAMETER GamePath
    Root of the game install. Defaults to C:\Sunrise.

.PARAMETER Configuration
    Which build to install. Release (default) or Debug.

.PARAMETER Restore
    Puts the original DLL back instead of installing.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -GamePath D:\Sunrise
    .\install.ps1 -Restore
#>
[CmdletBinding()]
param(
    [string]$GamePath = 'C:\Sunrise',
    [ValidateSet('Release', 'Debug')]
    [string]$Configuration = 'Release',
    [switch]$Restore
)

$ErrorActionPreference = 'Stop'

$binDir = Join-Path $GamePath 'bin\x64'
$target = Join-Path $binDir 'steam_api64.dll'
$backup = Join-Path $binDir 'steam_api64.dll.original'

if (-not (Test-Path $binDir)) {
    throw "No bin\x64 under $GamePath. Is the game installed there, and did the download finish?"
}

if ($Restore) {
    if (-not (Test-Path $backup)) {
        throw "No backup at $backup - nothing to restore."
    }
    Copy-Item $backup $target -Force
    Write-Host "Restored the original steam_api64.dll." -ForegroundColor Green
    return
}

$source = Join-Path $PSScriptRoot "build\x64\$Configuration\steam_api64.dll"
if (-not (Test-Path $source)) {
    throw "No build at $source. Run .\build.ps1 -Configuration $Configuration first."
}

# Only ever back up a DLL we did not produce. Once a Sunrise build is in place the original is
# already saved, and re-backing up would overwrite it with our own.
if (-not (Test-Path $backup)) {
    if (Test-Path $target) {
        Copy-Item $target $backup
        Write-Host "Backed up the original to steam_api64.dll.original" -ForegroundColor Cyan
    } else {
        Write-Host "No existing steam_api64.dll found; nothing to back up." -ForegroundColor Yellow
    }
} else {
    Write-Host "Backup already exists, leaving it alone." -ForegroundColor DarkGray
}

Copy-Item $source $target -Force

$info = Get-Item $target
Write-Host ""
Write-Host "Installed to $target" -ForegroundColor Green
Write-Host "Size:  $([math]::Round($info.Length / 1MB, 2)) MB"
Write-Host "Built: $($info.LastWriteTime)"

$settings = Join-Path $binDir 'Sunrise\settings.json'
if (Test-Path $settings) {
    Write-Host "Settings: $settings"
} else {
    Write-Host "Settings will appear at $settings after the first launch."
}
Write-Host ""
Write-Host "Launch: $(Join-Path $GamePath 'destiny2.exe')"
