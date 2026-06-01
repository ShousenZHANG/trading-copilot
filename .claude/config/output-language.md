# Output Language Configuration

> Single source of truth for the output language used by user-facing reports and agent reasoning.
>
> Loaded into agent context via the agents' `Output language` rule. Edit this file to change the project-wide output language.

## Current setting

**Output language**: **Chinese (中文)**

## Rules (apply to every agent)

1. **User-facing reports** (analyst reports, RM rationale, Trader reasoning, PM thesis, assembled decision report, weekly review, advisor output) → **Chinese (中文)**.
2. **Internal Bull/Bear/Risk debate** → **English** (always; non-negotiable per [TradingAgents arXiv 2412.20138](https://arxiv.org/abs/2412.20138) — reasoning quality degrades in non-English).
3. **Always preserved in English regardless of report language**:
   - Ticker symbols (incl. exchange suffixes `.HK` `.T` `.AX` `.L` `.TO` `=F` `=X` `^`)
   - Indicator names (RSI, MACD, ATR, SMA, EMA, BBands, VWMA)
   - Price numbers and currency codes
   - FRED series IDs (DFII10, DGS10, T10YIE, DTWEXBGS, etc.)
   - Provider / vendor names (Yahoo Finance, Finnhub, Alpha Vantage, FRED, Exa)

## How to change

Edit the **Output language** value in this file. All agent prompts reference this file via the `Output language: see .claude/config/output-language.md` line. No need to edit individual agent files when switching the project language.

Valid values:
- `Chinese (中文)` (current default)
- `English`
- Any other ISO-language label — agents will follow the directive but quality varies

## History

- 2026-05-09: reverted to Chinese (中文). User confirmed preference.
- 2026-05-08: switched from Chinese to English (later reverted same week).
- 2026-04-27 (initial): Chinese (中文) for user-facing, English for internal debate.
