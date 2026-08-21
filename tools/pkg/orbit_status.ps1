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

# Anything outside the boot chain means the transition completed and there is nothing left to wait for.
$past = $states | Where-Object { $_ -notmatch '^bootflow:|^character:signin$|^cleanup$' }

Write-Host ""
Write-Host ("  state        : {0}" -f $last)
Write-Host ("  game clock   : {0}s   tasks completed: {1}" -f [int]([int]$t / 1000), $done)
Write-Host ("  F8 capture   : {0}   reads logged: {1}" -f $capture, $reads)
Write-Host ("  log last grew: {0}s ago" -f $age)
Write-Host ""
if ($past) {
    Write-Host "  DONE - reached '$($past -join ", ")'. Close it whenever." -ForegroundColor Green
} elseif ($age -gt 45) {
    Write-Host "  STOPPED - nothing logged for ${age}s. That is a real stall; close it." -ForegroundColor Red
} else {
    Write-Host "  STILL WORKING - the log grew ${age}s ago. Keep waiting." -ForegroundColor Cyan
}
