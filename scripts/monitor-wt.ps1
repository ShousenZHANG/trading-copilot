# Trading Copilot monitor — Windows Terminal split-pane variant.
#
# Opens ONE Windows Terminal window with 3 panes side by side:
#   Left  : file watcher
#   Top R : latest file tailer
#   Bot R : run-folder size + agent-count summary
#
# Requires Windows Terminal (`wt.exe`). Install via:
#     winget install Microsoft.WindowsTerminal
#
# Usage:
#     cd D:\trading-copilot
#     .\scripts\monitor-wt.ps1 NVDA
#     .\scripts\monitor-wt.ps1 NVDA -Date 2026-04-27

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Ticker,

    [string]$Date = (Get-Date -Format 'yyyy-MM-dd'),

    [int]$RefreshSeconds = 5
)

if (-not (Get-Command wt.exe -ErrorAction SilentlyContinue)) {
    Write-Host '[monitor-wt] Windows Terminal (wt) not found.' -ForegroundColor Red
    Write-Host '[monitor-wt] Install:  winget install Microsoft.WindowsTerminal' -ForegroundColor Yellow
    Write-Host '[monitor-wt] Or use the multi-window variant:' -ForegroundColor Yellow
    Write-Host '              .\scripts\monitor.ps1 $Ticker' -ForegroundColor Yellow
    exit 1
}

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RunDir = Join-Path $ProjectRoot "data\runs\$Ticker-$Date"

if (-not (Test-Path $RunDir)) {
    New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
}

# Build inline PowerShell scripts for each pane
$Watcher = "while (`$true) { Clear-Host; Write-Host '=== Pipeline Files: $Ticker $Date ===' -ForegroundColor Cyan; Write-Host (`"Time: `" + (Get-Date -Format 'HH:mm:ss')) -ForegroundColor Yellow; `$f = Get-ChildItem -Path '$RunDir' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime; if (-not `$f) { Write-Host '(waiting...)' -ForegroundColor DarkGray } else { `$f | Format-Table -AutoSize Name, @{l='Time';e={`$_.LastWriteTime.ToString('HH:mm:ss')}}, @{l='KB';e={[math]::Round(`$_.Length/1KB,1)}}; Write-Host (`"Total: `" + `$f.Count) -ForegroundColor Green }; Start-Sleep -Seconds $RefreshSeconds }"

$Tailer = "while (`$true) { Clear-Host; `$l = Get-ChildItem -Path '$RunDir' -Filter '*.md' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if (`$l) { Write-Host (`"=== `" + `$l.Name + ' @ ' + `$l.LastWriteTime.ToString('HH:mm:ss') + ' ===') -ForegroundColor Cyan; Get-Content `$l.FullName -Tail 25 } else { Write-Host '=== waiting for first .md ===' -ForegroundColor Yellow }; Start-Sleep -Seconds $RefreshSeconds }"

$Summary = "while (`$true) { Clear-Host; Write-Host '=== Run Summary: $Ticker $Date ===' -ForegroundColor Cyan; `$f = Get-ChildItem -Path '$RunDir' -ErrorAction SilentlyContinue; if (`$f) { `$total = (`$f | Measure-Object Length -Sum).Sum; Write-Host (`"Files:  `" + `$f.Count) -ForegroundColor Green; Write-Host (`"Total:  `" + [math]::Round(`$total/1KB,1) + ' KB') -ForegroundColor Green; Write-Host ''; Write-Host 'Expected stages (√ = done):' -ForegroundColor Yellow; `$expected = '00-past-context','01-market','02-social','03-news','04-fundamentals','debate_history','06-research-plan','07-trader-proposal','risk_debate_history','08-portfolio-decision'; foreach (`$e in `$expected) { `$found = `$f | Where-Object { `$_.Name -like `"`$e*`" }; if (`$found) { Write-Host (`"  [done] `" + `$e) -ForegroundColor Green } else { Write-Host (`"  [....] `" + `$e) -ForegroundColor DarkGray } } } else { Write-Host '(waiting...)' -ForegroundColor DarkGray }; Start-Sleep -Seconds $RefreshSeconds }"

# wt syntax: wt.exe new-tab cmd; split-pane cmd; split-pane cmd
& wt.exe new-tab --title "Watcher [$Ticker]" powershell -NoExit -NoProfile -Command $Watcher `; split-pane -V --title "Latest [$Ticker]" powershell -NoExit -NoProfile -Command $Tailer `; split-pane -H --title "Summary [$Ticker]" powershell -NoExit -NoProfile -Command $Summary

Write-Host '[monitor-wt] Opened Windows Terminal with 3 panes.' -ForegroundColor Green
