# Watch a launch make progress while the window looks frozen.
#
# Going to orbit blocks the mainloop for tens of seconds at a time, so Windows greys the window out
# and it reads as a hang. It is not one - the log keeps recording state changes and task
# completions throughout. Both orbit attempts on 2026-08-21 were closed within eight seconds of a
# 25-second task finishing, with nothing pending.
#
# Run this in a second terminal, launch the game, and judge by what it prints rather than by the
# window. Ctrl+C to stop.
#
#   .\watch_load.ps1
#   .\watch_load.ps1 -All        # every line, not just the interesting ones
param(
    [string]$Log = "C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log",
    [switch]$All
)

# A launch rotates the log, so wait for the new one rather than tailing a file that is about to be
# replaced. Without this the tail silently follows the previous launch's file handle.
if (Test-Path $Log) {
    $was = (Get-Item $Log).LastWriteTime
    Write-Host "waiting for a fresh launch (current log last written $was)..." -ForegroundColor DarkGray
    while ((Get-Item $Log -ErrorAction SilentlyContinue).LastWriteTime -le $was) { Start-Sleep -Milliseconds 500 }
}
Write-Host "following $Log" -ForegroundColor Cyan

$interesting = "Entering state|task_manager|state:cleanup|hitch detected|bootflow stage|ev=shutdown|activity:"
Get-Content $Log -Wait -Tail 0 | ForEach-Object {
    if (-not $All -and $_ -notmatch $interesting) { return }
    $t = if ($_ -match '\bt=(\d+)\b') { "{0,7:N0}s" -f ([int]$Matches[1] / 1000) } else { "       " }
    $line = $_ -replace '^.*?text=', '' -replace '^client level=\w+ t=\d+ ', ''
    $colour = switch -Regex ($_) {
        'hitch detected'  { 'Yellow'; break }
        'Entering state'  { 'Cyan'; break }
        'Completed task'  { 'Green'; break }
        default           { 'Gray' }
    }
    Write-Host "$t  $line" -ForegroundColor $colour
}
