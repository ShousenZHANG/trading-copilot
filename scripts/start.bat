@echo off
REM Trading Copilot launcher for Windows cmd.
REM
REM Loads D:\trading-copilot\.env into the current cmd session,
REM then starts Claude Code so MCP servers can read API keys via
REM ${VAR} substitution in .mcp.json.
REM
REM Usage:
REM     cd D:\trading-copilot
REM     scripts\start.bat

setlocal enabledelayedexpansion

set "PROJECT_DIR=%~dp0.."
pushd "%PROJECT_DIR%"

if not exist ".env" (
    echo [warning] .env not found - starting without it
    echo           MCPs that need API keys will fail.
    goto :launch
)

echo [start.bat] Loading .env
set /a LOADED=0
for /f "usebackq tokens=* eol=#" %%a in (".env") do (
    set "line=%%a"
    REM skip empty lines
    if defined line (
        REM split at first '='
        for /f "tokens=1,* delims==" %%k in ("!line!") do (
            set "name=%%k"
            set "value=%%l"
            REM strip surrounding quotes
            if defined value (
                set "value=!value:"=!"
                set "value=!value:'=!"
                if defined name (
                    if not "!value!"=="" (
                        set "!name!=!value!"
                        set /a LOADED+=1
                    )
                )
            )
        )
    )
)
echo [start.bat] Loaded !LOADED! env var(s)

:launch
echo [start.bat] Launching claude in %PROJECT_DIR%
claude
endlocal
