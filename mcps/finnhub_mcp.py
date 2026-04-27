#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp[cli]>=1.2.0",
#   "httpx>=0.27.0",
# ]
# ///
"""Thin Finnhub MCP server.

A direct Python wrapper over the Finnhub REST API exposed as an MCP server.
Replaces the upstream `finnhub-mcp` npm package, which has a Windows path
bug (it spawns child processes without quoting `C:\\Program Files\\nodejs\\`
which Windows then truncates at the space).

Run via uv with inline PEP 723 dependencies — no separate install step.

Usage as MCP server (.mcp.json):
    {
      "mcpServers": {
        "finnhub": {
          "command": "cmd",
          "args": ["/c", "uv", "run", "D:/trading-copilot/mcps/finnhub_mcp.py"],
          "env": { "FINNHUB_API_KEY": "${FINNHUB_API_KEY}" }
        }
      }
    }

Get a free key (60 req/min): https://finnhub.io/register
API docs: https://finnhub.io/docs/api
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
BASE_URL = "https://finnhub.io/api/v1"
TIMEOUT_SECONDS = 15

mcp = FastMCP("finnhub")


def _require_key() -> None:
    if not API_KEY:
        raise RuntimeError(
            "FINNHUB_API_KEY environment variable not set. "
            "Get a free key at https://finnhub.io/register and put it in .env."
        )


def _get(endpoint: str, **params: Any) -> Any:
    _require_key()
    params["token"] = API_KEY
    url = f"{BASE_URL}/{endpoint}"
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200]
        raise RuntimeError(
            f"Finnhub API error {e.response.status_code} for /{endpoint}: {body}"
        ) from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"Finnhub API request failed for /{endpoint}: {e}") from e


def _default_date_range(look_back_days: int = 7) -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    return (today - timedelta(days=look_back_days)).isoformat(), today.isoformat()


# ---------------------------------------------------------------------------
# Quote / market data
# ---------------------------------------------------------------------------


@mcp.tool()
def get_quote(symbol: str) -> dict:
    """Real-time quote for a stock symbol.

    Returns dict with c=current, h=high, l=low, o=open, pc=prev_close, d=change,
    dp=change_pct, t=unix_ts.
    """
    return _get("quote", symbol=symbol.upper())


# ---------------------------------------------------------------------------
# Company profile + fundamentals
# ---------------------------------------------------------------------------


@mcp.tool()
def get_company_profile(symbol: str) -> dict:
    """Company profile: name, country, currency, exchange, industry, IPO, market cap."""
    return _get("stock/profile2", symbol=symbol.upper())


@mcp.tool()
def get_basic_financials(symbol: str, metric_type: str = "all") -> dict:
    """Company basic financials: P/E, P/S, ROE, margins, debt ratios, etc.

    metric_type: 'all' (default) | 'price' | 'valuation' | 'margin'.
    """
    return _get("stock/metric", symbol=symbol.upper(), metric=metric_type)


@mcp.tool()
def get_financials_reported(
    symbol: str,
    freq: str = "annual",
) -> dict:
    """As-reported financial statements (income / balance / cashflow).

    freq: 'annual' | 'quarterly'.
    """
    return _get("stock/financials-reported", symbol=symbol.upper(), freq=freq)


@mcp.tool()
def get_recommendation_trends(symbol: str) -> list:
    """Sell-side analyst recommendation trends (last 4 months).

    Returns list of dicts with strongBuy, buy, hold, sell, strongSell, period.
    """
    return _get("stock/recommendation", symbol=symbol.upper())


@mcp.tool()
def get_price_target(symbol: str) -> dict:
    """Latest analyst price target consensus (target_high, target_low, target_mean,
    target_median, last_updated, num_analysts).
    """
    return _get("stock/price-target", symbol=symbol.upper())


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


@mcp.tool()
def get_company_news(
    symbol: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> list:
    """Company news within date range. Dates as YYYY-MM-DD. Default: last 7 days.

    Returns list of news items (datetime, headline, source, summary, url, image, related).
    """
    if not from_date or not to_date:
        f, t = _default_date_range(7)
        from_date = from_date or f
        to_date = to_date or t
    return _get(
        "company-news",
        symbol=symbol.upper(),
        **{"from": from_date, "to": to_date},
    )


@mcp.tool()
def get_general_news(category: str = "general") -> list:
    """General market news.

    category: 'general' | 'forex' | 'crypto' | 'merger'.
    """
    return _get("news", category=category)


@mcp.tool()
def get_news_sentiment(symbol: str) -> dict:
    """News sentiment score (Finnhub-unique feature).

    Returns dict with buzz (article counts) + sentiment scores from 0 to 1
    (bullish vs bearish ratio across all news in the window).
    """
    return _get("news-sentiment", symbol=symbol.upper())


# ---------------------------------------------------------------------------
# Insider activity
# ---------------------------------------------------------------------------


@mcp.tool()
def get_insider_transactions(
    symbol: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """Insider transactions (Form 4 filings) for a symbol.

    Default range: last 90 days.
    """
    if not from_date or not to_date:
        f, t = _default_date_range(90)
        from_date = from_date or f
        to_date = to_date or t
    return _get(
        "stock/insider-transactions",
        symbol=symbol.upper(),
        **{"from": from_date, "to": to_date},
    )


@mcp.tool()
def get_insider_sentiment(
    symbol: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """Aggregated insider sentiment (monthly buying vs selling pressure).

    Default range: last 365 days.
    """
    if not from_date or not to_date:
        f, t = _default_date_range(365)
        from_date = from_date or f
        to_date = to_date or t
    return _get(
        "stock/insider-sentiment",
        symbol=symbol.upper(),
        **{"from": from_date, "to": to_date},
    )


# ---------------------------------------------------------------------------
# Earnings + calendar
# ---------------------------------------------------------------------------


@mcp.tool()
def get_earnings_calendar(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict:
    """Upcoming earnings releases.

    Default range: today through next 30 days. Filter by symbol or get full calendar.
    """
    if not from_date or not to_date:
        today = datetime.now(timezone.utc).date()
        from_date = from_date or today.isoformat()
        to_date = to_date or (today + timedelta(days=30)).isoformat()
    params: dict[str, Any] = {"from": from_date, "to": to_date}
    if symbol:
        params["symbol"] = symbol.upper()
    return _get("calendar/earnings", **params)


@mcp.tool()
def get_earnings_surprise(symbol: str, limit: int = 8) -> list:
    """Quarterly earnings surprises (last N quarters)."""
    return _get("stock/earnings", symbol=symbol.upper(), limit=limit)


@mcp.tool()
def get_economic_calendar() -> dict:
    """Macro economic event calendar (rate decisions, CPI, NFP, GDP)."""
    return _get("calendar/economic")


# ---------------------------------------------------------------------------
# Health-check
# ---------------------------------------------------------------------------


@mcp.tool()
def healthcheck() -> dict:
    """Verify the MCP can reach Finnhub and the API key is valid."""
    _require_key()
    try:
        result = _get("quote", symbol="AAPL")
        if not isinstance(result, dict) or "c" not in result:
            return {"ok": False, "reason": "Unexpected response shape", "result": result}
        return {"ok": True, "aapl_price": result.get("c"), "key_prefix": API_KEY[:6] + "..."}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def main() -> None:
    if not API_KEY:
        print(
            "WARNING: FINNHUB_API_KEY not set. Server will start but every tool will error.",
            file=sys.stderr,
        )
    mcp.run()


if __name__ == "__main__":
    main()
