# Trading Copilot pipeline monitor.
#
# Opens two PowerShell windows side-by-side:
#   1. File watcher  - lists files in data/runs/<TICKER>-<DATE>/ as they appear
#   2. Latest tailer - tails the most recently modified .md file in that dir
#
# Usage:
#     cd D:\trading-copilot
#     .\scripts\monitor.ps1 NVDA
#     .\scripts\monitor.ps1 NVDA -Date 2026-04-27   # explicit date
#
# Run BEFORE or AFTER you start /analyze in Claude Code. The monitor windows
# stay open until you close them; refreshes every 5 seconds.

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Ticker,

    [string]$Date = (Get-Date -Format 'yyyy-MM-dd'),

    [int]$RefreshSeconds = 5,

    [int]$TailLines = 30
)

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RunDir = Join-Path $ProjectRoot "data\runs\$Ticker-$Date"

if (-not (Test-Path $RunDir)) {
    Write-Host "[monitor] Creating $RunDir (does not exist yet)" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
}

Write-Host "[monitor] Watching $RunDir" -ForegroundColor Cyan
Write-Host "[monitor] Refresh interval: ${RefreshSeconds}s" -ForegroundColor Cyan

# ----------------------------------------------------------------------------
# Window 1: file list watcher
# ----------------------------------------------------------------------------
$WatcherCmd = @"
`$Host.UI.RawUI.WindowTitle = 'Trading Copilot - File Watcher [$Ticker $Date]'
while (`$true) {
    Clear-Host
    Write-Host '=== Pipeline Files: $Ticker $Date ===' -ForegroundColor Cyan
    Write-Host (`"Time: `" + (Get-Date -Format 'HH:mm:ss')) -ForegroundColor Yellow
    Write-Host (`"Dir:  $RunDir`") -ForegroundColor Gray
    Write-Host ''
    `$files = Get-ChildItem -Path '$RunDir' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime
    if (-not `$files) {
        Write-Host '(no files yet — waiting for analysts to start)' -ForegroundColor DarkGray
    } else {
        `$files | Format-Table -AutoSize @{l='Name';e={`$_.Name}}, @{l='Modified';e={`$_.LastWriteTime.ToString('HH:mm:ss')}}, @{l='Size(KB)';e={[math]::Round(`$_.Length/1KB,1)}}
        Write-Host (`"Total: `" + `$files.Count + ' files') -ForegroundColor Green
    }
    Write-Host ''
    Write-Host 'Expected sequence (13 stages):' -ForegroundColor DarkGray
    Write-Host '  00-past-context.md, 01-market.md, 02-social.md, 03-news.md,' -ForegroundColor DarkGray
    Write-Host '  04-fundamentals.md, debate_history.md, 06-research-plan.md,' -ForegroundColor DarkGray
    Write-Host '  07-trader-proposal.md, risk_debate_history.md, 08-portfolio-decision.md' -ForegroundColor DarkGray
    Start-Sleep -Seconds $RefreshSeconds
}
"@

Start-Process powershell -ArgumentList @('-NoExit', '-NoProfile', '-Command', $WatcherCmd)

Start-Sleep -Seconds 1

# ----------------------------------------------------------------------------
# Window 2: latest file tailer
# ----------------------------------------------------------------------------
$TailerCmd = @"
`$Host.UI.RawUI.WindowTitle = 'Trading Copilot - Latest Report [$Ticker $Date]'
while (`$true) {
    Clear-Host
    `$latest = Get-ChildItem -Path '$RunDir' -Filter '*.md' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (`$latest) {
        Write-Host (`"=== Latest: `" + `$latest.Name + ' (modified ' + `$latest.LastWriteTime.ToString('HH:mm:ss') + ') ===') -ForegroundColor Cyan
        Write-Host (`"Showing last $TailLines lines | Refresh: ${RefreshSeconds}s | $RunDir`") -ForegroundColor DarkGray
        Write-Host ''
        Get-Content -Path `$latest.FullName -Tail $TailLines -ErrorAction SilentlyContinue
    } else {
        Write-Host (`"=== Waiting for first .md in $RunDir ===`") -ForegroundColor Yellow
    }
    Start-Sleep -Seconds $RefreshSeconds
}
"@

Start-Process powershell -ArgumentList @('-NoExit', '-NoProfile', '-Command', $TailerCmd)

Write-Host ''
Write-Host '[monitor] Two monitor windows opened.' -ForegroundColor Green
Write-Host '[monitor] Close them when /analyze is done.' -ForegroundColor Green
Write-Host ''
Write-Host 'TIP: For a single-window split-pane view, install Windows Terminal:' -ForegroundColor DarkGray
Write-Host '     winget install Microsoft.WindowsTerminal' -ForegroundColor DarkGray
Write-Host '     then run:  .\scripts\monitor-wt.ps1 $Ticker' -ForegroundColor DarkGray
