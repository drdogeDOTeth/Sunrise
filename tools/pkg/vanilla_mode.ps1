# Move every patch layer we ever wrote aside, leaving the installed game exactly as shipped.
#
# This is the control that `revert_layer.ps1` cannot give: that one drops the top layer of a few
# named packages, while our work is spread across ~30 packages stacked up to 22 deep. Selecting a
# different character is *not* a control either - character select previews every character, so our
# patched content is resident whichever one is picked.
#
# Reversible: files are moved, never deleted, and moved back by -Restore. The originals underneath
# are untouched shipped layers, so nothing needs regenerating either way.
#
#   .\vanilla_mode.ps1                  # list what would move
#   .\vanilla_mode.ps1 -Confirm         # move them; game is now vanilla
#   .\vanilla_mode.ps1 -Restore         # put every one of them back
param(
    [string]$Packages = "C:\Sunrise\packages",
    # The install finished 2026-08-16; our first written layer is 2026-08-19. Anything newer than
    # this is ours. Video and audio packages legitimately contain plain blocks, so "has plain
    # blocks" would misidentify them - the write date is the honest discriminator.
    [datetime]$InstalledBefore = "2026-08-18",
    [switch]$Confirm,
    [switch]$Restore
)

$attic = Join-Path $Packages "_vanilla_test"
if (Get-Process destiny2 -ErrorAction SilentlyContinue) {
    Write-Error "destiny2 is running; close it before moving package files"
    exit 1
}

if ($Restore) {
    if (-not (Test-Path $attic)) { Write-Error "nothing in $attic to restore"; exit 1 }
    $files = Get-ChildItem $attic -Filter *.pkg
    $clash = $files | Where-Object { Test-Path (Join-Path $Packages $_.Name) }
    if ($clash) {
        Write-Error ("$($clash.Count) file(s) would overwrite a live layer of the same name: " +
                     "$($clash.Name -join ', '). Move those aside first.")
        exit 1
    }
    $files | ForEach-Object { Move-Item $_.FullName $Packages }
    "restored $($files.Count) layers"
    exit 0
}

$ours = Get-ChildItem (Join-Path $Packages "*.pkg") |
    Where-Object { $_.LastWriteTime -gt $InstalledBefore } |
    Sort-Object Name
if (-not $ours) { "nothing newer than $InstalledBefore - already vanilla"; exit 0 }

$bytes = ($ours | Measure-Object Length -Sum).Sum
$stems = ($ours | ForEach-Object { $_.BaseName -replace '_\d+$', '' } | Sort-Object -Unique)
"$($ours.Count) layers we wrote, across $($stems.Count) packages, $('{0:N0}' -f $bytes) B"
foreach ($stem in $stems) {
    $n = @($ours | Where-Object { ($_.BaseName -replace '_\d+$', '') -eq $stem })
    "  $stem : $($n.Count) layer(s) - $($n.BaseName -replace '.*_', '' -join ', ')"
}
if (-not $Confirm) { "`nNothing moved. Re-run with -Confirm to go vanilla."; exit 0 }

if (-not (Test-Path $attic)) { New-Item -ItemType Directory $attic | Out-Null }
$ours | ForEach-Object { Move-Item $_.FullName $attic }
"`nmoved $($ours.Count) layers to $attic - the game is now exactly as installed"
