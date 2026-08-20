# Move the newest patch layer of each named package aside, reverting to the one beneath it.
#
# Patches are additive and the loader takes the newest file, so removing the top file restores the
# previous state exactly - the layer below is self-contained. Moving rather than deleting keeps it
# reversible, which matters because a bad layer's bytes cannot be regenerated from the game: the
# originals were encrypted and were never dumped.
#
#   .\revert_layer.ps1                      # what would move, nothing touched
#   .\revert_layer.ps1 -Confirm             # move them
#   .\revert_layer.ps1 -Restore             # put the last reverted set back
param(
    [string[]]$Stems = @("w64_sandbox_037c", "w64_sandbox_037d", "w64_sandbox_0698",
                         "w64_sandbox_0699"),
    [string]$Packages = "C:\Sunrise\packages",
    [switch]$Confirm,
    [switch]$Restore
)

$attic = Join-Path $Packages "_reverted"
if (Get-Process destiny2 -ErrorAction SilentlyContinue) {
    Write-Error "destiny2 is running; close it before moving package files"
    exit 1
}

if ($Restore) {
    if (-not (Test-Path $attic)) { Write-Error "nothing in $attic to restore"; exit 1 }
    Get-ChildItem $attic -Filter *.pkg | ForEach-Object {
        Move-Item $_.FullName $Packages -Force
        "restored $($_.Name)"
    }
    exit 0
}

if (-not (Test-Path $attic)) { New-Item -ItemType Directory $attic | Out-Null }
foreach ($stem in $Stems) {
    $layers = Get-ChildItem (Join-Path $Packages "$stem`_*.pkg") -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.BaseName -replace '.*_', '') }
    if ($layers.Count -lt 2) { "  $stem : only $($layers.Count) layer, skipped"; continue }
    $top = $layers[-1]
    $under = $layers[-2]
    if ($Confirm) {
        Move-Item $top.FullName $attic -Force
        "  $stem : moved $($top.Name), now live on $($under.Name)"
    } else {
        "  $stem : would move $($top.Name), leaving $($under.Name)"
    }
}
if (-not $Confirm) { "`nNothing moved. Re-run with -Confirm to revert." }
