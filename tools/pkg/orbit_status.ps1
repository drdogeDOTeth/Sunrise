# One line: is the load still moving, and has it left cleanup yet?
#
# The window greys out during a world transition because the mainloop blocks, so it is not a
# usable signal. This reads the log instead and says whether to keep waiting or stop.
# Safe to run repeatedly while the game is frozen - it only reads the file.
param([string]$Log = "C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log")

if (-not (Test-Path $Log)) { Write-Host "no log yet - has the game started?" -ForegroundColor Yellow; exit }
# The game holds the log open for writing, so a plain read intermittently fails and returns nothing.
# Reporting that as "no progress" once contradicted a launch that had actually reached orbit.
$text = $null
try {
    $stream = [System.IO.FileStream]::new($Log, 'Open', 'Read', 'ReadWrite')
    try { $text = [System.IO.StreamReader]::new($stream).ReadToEnd() } finally { $stream.Dispose() }
} catch { }
if (-not $text) { Write-Host "log is locked right now - run it again in a second" -ForegroundColor Yellow; exit }
$states = [regex]::Matches($text, "Entering state '([^']+)'") | ForEach-Object { $_.Groups[1].Value }
$last = if ($states) { $states[-1] } else { '(none)' }
$t = ([regex]::Matches($text, '\bt=(\d+)\b') | Select-Object -Last 1).Groups[1].Value
$done = ([regex]::Matches($text, "Completed task 'ENUM\(\d+\)'")).Count
$reads = ([regex]::Matches($text, 'ev=package_trace stage=read')).Count
$capture = if ($text -match 'stage=capture result=started') { 'ARMED' } else { 'not armed - press F8' }
$age = [int](((Get-Date) - (Get-Item $Log).LastWriteTime).TotalSeconds)

# Judge by PROGRESS, not by reaching a named state. An earlier version called "DONE" as soon as
# anything past cleanup appeared, which made it useless for a Tower load - that happens long after
# setup:orbit. A deadlock is distinguished by the log going quiet and the state never advancing,
# not by which state it is in.
$models = ([regex]::Matches($text, 'ev=model_class_trace')).Count
$stateAge = if ($states) {
    $at = [regex]::Matches($text, "Entering state '$([regex]::Escape($last))'") | Select-Object -Last 1
    $tail = $text.Substring($at.Index)
    $clock = [regex]::Matches($tail, '\bt=(\d+)\b')
    if ($clock.Count -ge 2) { [int](([int]$clock[$clock.Count-1].Groups[1].Value - [int]$clock[0].Groups[1].Value) / 1000) } else { 0 }
} else { 0 }

Write-Host ""
Write-Host ("  state        : {0}   (in it for {1}s of game time)" -f $last, $stateAge)
Write-Host ("  game clock   : {0}s   tasks completed: {1}   models resolved: {2}" -f [int]([int]$t / 1000), $done, $models)
Write-Host ("  F8 capture   : {0}   reads logged: {1}" -f $capture, $reads)
Write-Host ("  log last grew: {0}s ago" -f $age)
Write-Host ""
if (-not (Get-Process destiny2 -ErrorAction SilentlyContinue)) {
    Write-Host "  GAME NOT RUNNING - this is the previous launch's log, ended in '$last'." -ForegroundColor DarkGray
} elseif ($age -gt 60) {
    Write-Host "  STALLED - nothing logged for ${age}s while '$last'. Real deadlock; close it." -ForegroundColor Red
} elseif ($age -gt 20) {
    Write-Host "  QUIET for ${age}s in '$last' - give it another minute before deciding." -ForegroundColor Yellow
} else {
    Write-Host "  WORKING - log grew ${age}s ago. Keep waiting." -ForegroundColor Cyan
}
if ($last -match '^activity:') {
    Write-Host "  (in a world load - these are much heavier than orbit; allow 2 minutes)" -ForegroundColor DarkGray
}
