<#
.SYNOPSIS
    Host Intake — bring a custom GLB onto the playable Warlock.

.DESCRIPTION
    Launches the intake desk (default) or runs the pipeline from the command line.
    Destiny must be closed to inject packages.

.EXAMPLE
    .\bring_guardian.ps1
    .\bring_guardian.ps1 -Inspect "D:\models\my_character.glb"
    .\bring_guardian.ps1 -Glb "D:\models\my_character.glb" -DryRun
    .\bring_guardian.ps1 -Glb "D:\models\my_character.glb" -Inject
#>
[CmdletBinding()]
param(
    [string]$Glb,
    [string]$Inspect,
    [switch]$DryRun,
    [switch]$Inject,
    [switch]$Preflight,
    [switch]$Snapshot,
    [switch]$Cli
)

$ErrorActionPreference = 'Stop'
$pkg = Join-Path $PSScriptRoot 'tools\pkg'
if (-not (Test-Path (Join-Path $pkg 'bring_guardian.py'))) {
    throw "bring_guardian.py not found under $pkg"
}

$pyArgs = @()
if ($Inspect) {
    $pyArgs += @('--inspect', $Inspect)
} elseif ($Glb -or $DryRun -or $Inject -or $Cli -or $Preflight) {
    if ($Glb) { $pyArgs += @('--glb', $Glb) }
    if ($DryRun) { $pyArgs += '--dry-run' }
    if ($Inject) { $pyArgs += '--inject' }
    if ($Preflight) { $pyArgs += '--preflight' }
    if ($Snapshot) { $pyArgs += '--snapshot' }
} else {
    $pyArgs += '--ui'
}

Push-Location $pkg
try {
    python .\bring_guardian.py @pyArgs
} finally {
    Pop-Location
}
