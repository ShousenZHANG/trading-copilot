# Launch either client from this checkout; provider values are never printed.
param(
    [ValidateSet("claude", "codex")][string]$Client = "claude",
    [string]$ProjectDir = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ClientArgs
)
$ErrorActionPreference = "Stop"
$resolvedProject = (Resolve-Path -LiteralPath $ProjectDir).Path
$envFile = Join-Path $resolvedProject ".env"
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in (Get-Content -LiteralPath $envFile -Encoding UTF8)) {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$') {
            $variableName = $matches[1]
            $variableValue = $matches[2].Trim('"').Trim("'")
            if ($variableValue -and -not [Environment]::GetEnvironmentVariable($variableName, "Process")) {
                [Environment]::SetEnvironmentVariable($variableName, $variableValue, "Process")
            }
        }
    }
}
Push-Location -LiteralPath $resolvedProject
try { & python (Join-Path $resolvedProject "scripts/start_client.py") $Client @ClientArgs; exit $LASTEXITCODE }
finally { Pop-Location }
