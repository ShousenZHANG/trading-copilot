# Trading Copilot launcher for Windows PowerShell.
#
# Loads D:\trading-copilot\.env into the current PowerShell session,
# then starts Claude Code so MCP servers can read API keys via ${VAR}
# substitution in .mcp.json.
#
# Usage:
#     cd D:\trading-copilot
#     .\scripts\start.ps1
#
# Or, to bypass execution policy:
#     powershell -ExecutionPolicy Bypass -File scripts\start.ps1

param(
    [string]$ProjectDir = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
)

$envFile = Join-Path $ProjectDir ".env"

if (-not (Test-Path $envFile)) {
    Write-Host "[warning] .env not found at $envFile - starting without it" -ForegroundColor Yellow
    Write-Host "          MCPs that need API keys will fail." -ForegroundColor Yellow
} else {
    Write-Host "[start.ps1] Loading $envFile" -ForegroundColor Cyan
    $loaded = 0
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { return }
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$') {
            $name  = $matches[1]
            $value = $matches[2].Trim('"').Trim("'")
            if ($value -ne '') {
                Set-Item -Path "Env:$name" -Value $value
                $loaded++
            }
        }
    }
    Write-Host "[start.ps1] Loaded $loaded env var(s)" -ForegroundColor Cyan
}

Set-Location $ProjectDir
Write-Host "[start.ps1] Launching claude in $ProjectDir" -ForegroundColor Cyan
& claude
