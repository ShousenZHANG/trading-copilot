#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp[cli]>=1.2.0",
#   "akshare>=1.16.0",
#   "pandas>=2.0.0",
# ]
# ///
"""Thin AkShare MCP server — A-share / HK / index coverage.

NO API KEY REQUIRED. NO REGISTRATION. NO TOKEN.
=================================================
AkShare scrapes public endpoints (EastMoney / Sina / THS) and needs **zero**
credentials. That is the entire reason it is the primary China-data source here
instead of Tushare, which needs a token and gates most useful endpoints behind
a points system. Nothing in this file reads an environment variable, and there
is no secret to leak.

Why this exists: Finnhub and Yahoo Finance coverage of ``.SS`` / ``.SZ`` is
thin-to-absent. An empty payload from those vendors means "not covered", not
"nothing happened" (see docs/mcp-fallback.md). This server closes that hole.

Shape mirrors ``mcps/finnhub_mcp.py``: one file, PEP 723 inline metadata, run
via ``uv run --no-project --quiet --script``. Dependencies never touch the repo
environment — ``scripts/`` stays stdlib-only.

Symbol convention
-----------------
==================  ==============================================
``600519.SS``       Shanghai main board (``.SH`` accepted as alias)
``688111.SS``       STAR market
``000001.SZ``       Shenzhen main board
``300750.SZ``       ChiNext
``430047.BJ``       Beijing Stock Exchange
``00700.HK``        Hong Kong (5 digits, zero-padded)
``000001.SH``       SSE Composite index (``sh000001`` also accepted)
``399001.SZ``       SZSE Component index (``sz399001`` also accepted)
``HSI``             Hang Seng Index
==================  ==============================================

A bare 6-digit A-share code is accepted and the exchange is inferred from the
prefix (6/9 -> SH, 0/2/3 -> SZ, 4/8 -> BJ). Bare index codes are ambiguous
(``000001`` is both Ping An Bank and the SSE Composite), so ``get_index_quote``
prefers an explicit suffix.

Failure contract
----------------
Every tool returns a JSON-serialisable ``dict``. On upstream failure it returns
``{"error": "..."}`` instead of raising, so an analyst agent can degrade to the
next link in the fallback chain rather than dying mid-pipeline.

Usage as MCP server (.mcp.json) — ships DISABLED as ``_akshare``:
    {
      "mcpServers": {
        "_akshare": {
          "command": "cmd",
          "args": ["/c", "uv", "run", "--no-project", "--quiet", "--script",
                   "D:/trading-copilot/mcps/akshare_mcp.py"]
        }
      }
    }

Enable with ``python scripts/enable_mcp.py akshare``.

CLI
---
    python mcps/akshare_mcp.py --self-test    # deterministic symbol-parser tests
    python mcps/akshare_mcp.py --help
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
try:
    from runtime import force_utf8_stdio  # type: ignore

    force_utf8_stdio()
except Exception:  # pragma: no cover - standalone copy without scripts/runtime.py
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

try:
    from mcp.server.fastmcp import FastMCP

    mcp: Optional[Any] = FastMCP("akshare")
    tool = mcp.tool
except ImportError:  # inspection / --self-test path without the MCP SDK installed
    mcp = None

    def tool() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """No-op stand-in for ``FastMCP.tool`` so the CLI path stays importable."""

        def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn

        return _decorator


# ---------------------------------------------------------------------------
# Symbol validation / normalisation (deterministic — covered by --self-test)
# ---------------------------------------------------------------------------

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9]+(\.[A-Za-z0-9]+)?$")
_PREFIXED_RE = re.compile(r"^(sh|sz|bj)(\d{6})$", re.IGNORECASE)
_SUFFIX_ALIASES = {"SS": "SH", "SH": "SH", "SZ": "SZ", "BJ": "BJ", "HK": "HK"}
_A_SHARE_MARKETS = ("SH", "SZ", "BJ")
_MAX_SYMBOL_LEN = 16


@dataclass(frozen=True)
class Symbol:
    """Normalised instrument identifier. Immutable value object."""

    code: str
    market: str
    raw: str

    @property
    def prefixed(self) -> str:
        """AkShare index form, e.g. ``sh000001``."""
        return f"{self.market.lower()}{self.code}"

    @property
    def canonical(self) -> str:
        return f"{self.code}.{self.market}" if self.market else self.code


def _infer_a_share_market(code: str) -> str:
    """Infer the exchange for a bare 6-digit A-share code."""
    head = code[0]
    if head in "69":
        return "SH"
    if head in "023":
        return "SZ"
    if head in "48":
        return "BJ"
    raise ValueError(f"cannot infer exchange for A-share code: {code!r}")


def normalise_symbol(value: str, *, default_market: str = "") -> Symbol:
    """Validate and normalise an instrument symbol.

    Rejects anything that is not plain alphanumerics plus at most one dot. This
    is the injection guard: the returned ``code`` is what gets handed to
    AkShare, so no separator, quote, whitespace, or path character can survive.
    Same spirit as ``scripts/ticker.py::validate_ticker_component``.
    """
    if not isinstance(value, str):
        raise ValueError(f"symbol must be a string, got {type(value).__name__}")
    raw = value.strip()
    if not raw:
        raise ValueError("symbol must be a non-empty string")
    if len(raw) > _MAX_SYMBOL_LEN:
        raise ValueError(f"symbol exceeds {_MAX_SYMBOL_LEN} chars: {value!r}")
    if not _SYMBOL_RE.fullmatch(raw):
        raise ValueError(f"symbol contains unsafe characters: {value!r}")

    prefixed = _PREFIXED_RE.fullmatch(raw)
    if prefixed:
        return Symbol(code=prefixed.group(2), market=prefixed.group(1).upper(), raw=raw)

    if "." in raw:
        code, _, suffix = raw.partition(".")
        market = _SUFFIX_ALIASES.get(suffix.upper())
        if market is None:
            raise ValueError(f"unsupported exchange suffix: {value!r}")
        if market == "HK":
            return Symbol(code=code.zfill(5), market="HK", raw=raw)
        if not code.isdigit():
            raise ValueError(f"A-share code must be numeric: {value!r}")
        return Symbol(code=code, market=market, raw=raw)

    if raw.isdigit():
        if len(raw) == 6:
            return Symbol(code=raw, market=_infer_a_share_market(raw), raw=raw)
        if len(raw) <= 5:
            return Symbol(code=raw.zfill(5), market="HK", raw=raw)
        raise ValueError(f"unrecognised numeric symbol length: {value!r}")

    return Symbol(code=raw.upper(), market=default_market, raw=raw)


# ---------------------------------------------------------------------------
# AkShare access + JSON coercion
# ---------------------------------------------------------------------------

_FIELD_MAP = {
    "代码": "code", "股票代码": "code", "名称": "name", "简称": "name",
    "最新价": "price", "最新": "price", "收盘": "close", "今开": "open",
    "开盘": "open", "最高": "high", "最低": "low", "昨收": "prev_close",
    "涨跌幅": "change_pct", "涨幅": "change_pct", "涨跌额": "change",
    "涨跌": "change", "成交量": "volume", "总手": "volume",
    "成交额": "turnover", "金额": "turnover", "换手率": "turnover_rate",
    "换手": "turnover_rate", "振幅": "amplitude", "量比": "volume_ratio",
    "市盈率-动态": "pe_ttm", "市净率": "pb", "总市值": "market_cap",
    "流通市值": "float_market_cap", "日期": "date", "均价": "vwap",
}


def _akshare() -> Any:
    """Lazy AkShare import. Kept out of module scope so ``--help`` never needs it."""
    try:
        import akshare  # noqa: PLC0415 - intentional lazy import
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "akshare is not installed. Run this server via "
            "`uv run --no-project --quiet --script mcps/akshare_mcp.py` so the "
            "PEP 723 inline dependencies are provisioned in an isolated env."
        ) from exc
    return akshare


def _jsonable(value: Any) -> Any:
    """Coerce pandas / numpy scalars into JSON-serialisable primitives."""
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        return None if isinstance(value, float) and value != value else value
    for attr in ("isoformat", "item"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return _jsonable(method())
            except Exception:
                break
    return str(value)


def _map_row(row: dict[str, Any]) -> dict[str, Any]:
    """Translate known Chinese column labels to stable English keys."""
    return {_FIELD_MAP.get(str(k), str(k)): _jsonable(v) for k, v in row.items()}


def _first_frame(chain: list[tuple[str, Callable[[], Any]]]) -> tuple[Any, str]:
    """Multi-source degradation chain: return the first non-empty DataFrame.

    Pattern borrowed from hsliuping/TradingAgents-CN (Apache-2.0) — AkShare
    endpoints drift, so never depend on a single upstream function name.
    """
    problems: list[str] = []
    for label, fetch in chain:
        try:
            frame = fetch()
        except Exception as exc:
            problems.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
        if frame is None or getattr(frame, "empty", True):
            problems.append(f"{label}: empty payload (symbol likely not covered)")
            continue
        return frame, label
    raise RuntimeError("; ".join(problems) or "no data source succeeded")


def _filter_by_code(frame: Any, code: str) -> Any:
    """Row-select a spot snapshot table by instrument code."""
    for column in ("代码", "code", "symbol"):
        if column in frame.columns:
            hit = frame[frame[column].astype(str).str.zfill(len(code)) == code]
            if not hit.empty:
                return hit.iloc[0]
    raise RuntimeError(f"code {code} not present in snapshot ({len(frame)} rows)")


_PERIOD_DAYS = {"1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731}


def _date_window(period: str) -> tuple[str, str]:
    """Resolve a period label into AkShare ``YYYYMMDD`` start/end strings."""
    days = _PERIOD_DAYS.get(period)
    if days is None:
        raise ValueError(
            f"unsupported period {period!r}; use one of {sorted(_PERIOD_DAYS)}"
        )
    today = date.today()
    return (today - timedelta(days=days)).strftime("%Y%m%d"), today.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool()
def get_cn_quote(symbol: str) -> dict:
    """Real-time A-share quote. Keyless.

    symbol: ``600519.SS`` | ``000001.SZ`` | ``430047.BJ`` | bare ``600519``.
    Returns price, change_pct, open/high/low, prev_close, volume, turnover.
    On failure returns ``{"error": ...}``.
    """
    try:
        sym = normalise_symbol(symbol)
        if sym.market not in _A_SHARE_MARKETS:
            raise ValueError(f"not an A-share symbol: {symbol!r} (use get_hk_quote)")
        ak = _akshare()
        frame, source = _first_frame([
            ("stock_bid_ask_em", lambda: ak.stock_bid_ask_em(symbol=sym.code)),
            ("stock_zh_a_spot_em", ak.stock_zh_a_spot_em),
        ])
        if source == "stock_bid_ask_em":
            payload = _map_row(dict(zip(frame["item"], frame["value"])))
        else:
            payload = _map_row(_filter_by_code(frame, sym.code).to_dict())
        return {"symbol": sym.canonical, "market": sym.market, "source": source, **payload}
    except Exception as exc:
        return {"error": f"get_cn_quote({symbol!r}) failed: {exc}"}


@tool()
def get_cn_history(symbol: str, period: str = "3mo") -> dict:
    """Daily OHLCV history for an A-share, forward-adjusted (qfq). Keyless.

    period: '1mo' | '3mo' (default) | '6mo' | '1y' | '2y'. Per the repo
    anti-stale rule the default is tactical (3mo), never '1y'/'max'.
    ``last_bar_date`` + ``bar_count`` are returned so the caller can run the
    data-freshness gate. On failure returns ``{"error": ...}``.
    """
    try:
        sym = normalise_symbol(symbol)
        if sym.market not in _A_SHARE_MARKETS:
            raise ValueError(f"not an A-share symbol: {symbol!r}")
        start, end = _date_window(period)
        ak = _akshare()
        frame, source = _first_frame([
            ("stock_zh_a_hist", lambda: ak.stock_zh_a_hist(
                symbol=sym.code, period="daily",
                start_date=start, end_date=end, adjust="qfq")),
        ])
        bars = [_map_row(row) for row in frame.to_dict(orient="records")]
        return {
            "symbol": sym.canonical,
            "period": period,
            "adjust": "qfq",
            "source": source,
            "bar_count": len(bars),
            "last_bar_date": bars[-1].get("date") if bars else None,
            "bars": bars,
        }
    except Exception as exc:
        return {"error": f"get_cn_history({symbol!r}, {period!r}) failed: {exc}"}


@tool()
def get_hk_quote(symbol: str) -> dict:
    """Real-time Hong Kong equity quote. Keyless.

    symbol: ``00700.HK`` | bare ``700`` (zero-padded to 5 digits).
    On failure returns ``{"error": ...}``.
    """
    try:
        sym = normalise_symbol(symbol, default_market="HK")
        if sym.market != "HK":
            raise ValueError(f"not a HK symbol: {symbol!r} (use get_cn_quote)")
        ak = _akshare()
        frame, source = _first_frame([
            ("stock_hk_spot_em", ak.stock_hk_spot_em),
            ("stock_hk_spot", ak.stock_hk_spot),
        ])
        payload = _map_row(_filter_by_code(frame, sym.code).to_dict())
        return {"symbol": sym.canonical, "market": "HK", "source": source, **payload}
    except Exception as exc:
        return {"error": f"get_hk_quote({symbol!r}) failed: {exc}"}


@tool()
def get_index_quote(symbol: str) -> dict:
    """Real-time index quote (CN + HK). Keyless.

    symbol: ``000001.SH`` (SSE Composite) | ``399001.SZ`` (SZSE Component) |
    ``sh000001`` | ``HSI``. Bare 6-digit codes are ambiguous with equities —
    pass an explicit suffix. On failure returns ``{"error": ...}``.
    """
    try:
        sym = normalise_symbol(symbol, default_market="HK")
        ak = _akshare()
        if sym.market == "HK":
            chain = [("stock_hk_index_spot_em", ak.stock_hk_index_spot_em)]
        else:
            chain = [
                ("stock_zh_index_spot_em",
                 lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数")),
                ("stock_zh_index_spot_sina", ak.stock_zh_index_spot_sina),
            ]
        frame, source = _first_frame(chain)
        payload = _map_row(_filter_by_code(frame, sym.code).to_dict())
        return {"symbol": sym.canonical, "kind": "index", "source": source, **payload}
    except Exception as exc:
        return {"error": f"get_index_quote({symbol!r}) failed: {exc}"}


@tool()
def healthcheck() -> dict:
    """Verify AkShare is importable and reachable. No API key is involved."""
    try:
        ak = _akshare()
    except RuntimeError as exc:
        return {"ok": False, "reason": str(exc)}
    quote = get_cn_quote("600519.SS")
    if "error" in quote:
        return {"ok": False, "reason": quote["error"], "akshare": getattr(ak, "__version__", "?")}
    return {"ok": True, "akshare": getattr(ak, "__version__", "?"), "probe": quote.get("price")}


# ---------------------------------------------------------------------------
# Self-test — deterministic, no network, no clock
# ---------------------------------------------------------------------------


_ACCEPT_CASES: tuple[tuple[str, str, str], ...] = (
    ("600519.SS", "600519", "SH"),
    ("600519.SH", "600519", "SH"),
    ("688111.ss", "688111", "SH"),
    ("000001.SZ", "000001", "SZ"),
    ("300750.sz", "300750", "SZ"),
    ("430047.BJ", "430047", "BJ"),
    ("00700.HK", "00700", "HK"),
    ("700.hk", "00700", "HK"),
    ("sh000001", "000001", "SH"),
    ("SZ399001", "399001", "SZ"),
    ("600519", "600519", "SH"),
    ("000001", "000001", "SZ"),
    ("430047", "430047", "BJ"),
    ("700", "00700", "HK"),
    ("HSI", "HSI", "HK"),
)

# Injection / malformed inputs that must never reach AkShare.
_REJECT_CASES: tuple[str, ...] = (
    "", "   ", "600519;rm -rf /", "../../etc/passwd", "600519.US",
    "600519 .SS", "600519\n.SS", "'600519'", "600519.SS.SZ", "6005190000",
    "AAAAAAAAAAAAAAAAAAAAA", "600519/..", "600519%00", "600519|ls", "600519&&ls",
)


def _run_accept_cases() -> tuple[int, int]:
    passed = 0
    for raw, code, market in _ACCEPT_CASES:
        try:
            sym: Any = normalise_symbol(raw, default_market="HK")
            hit = sym.code == code and sym.market == market
        except ValueError as exc:
            sym, hit = exc, False
        passed += hit
        print(f"  {'ok ' if hit else 'XX '} normalise_symbol({raw!r}) -> {sym} "
              f"(expected {code}.{market})")
    return passed, len(_ACCEPT_CASES)


def _run_reject_cases() -> tuple[int, int]:
    passed = 0
    for raw in _REJECT_CASES:
        try:
            normalise_symbol(raw, default_market="HK")
            hit, detail = False, "ACCEPTED (should have been rejected)"
        except ValueError as exc:
            hit, detail = True, str(exc)
        passed += hit
        print(f"  {'ok ' if hit else 'XX '} normalise_symbol({raw!r}) rejected [{detail}]")
    return passed, len(_REJECT_CASES)


def _run_derived_cases() -> tuple[int, int]:
    cases = (
        (normalise_symbol("000001.SH").prefixed == "sh000001", "prefixed(000001.SH)==sh000001"),
        (normalise_symbol("00700.HK").canonical == "00700.HK", "canonical(00700.HK)"),
        (_jsonable(float("nan")) is None, "_jsonable(nan) is None"),
        (_jsonable(None) is None, "_jsonable(None) is None"),
        (_map_row({"代码": "600519", "最新价": 1.5}) == {"code": "600519", "price": 1.5},
         "_map_row translates EastMoney columns"),
        (_PERIOD_DAYS["3mo"] == 92, "3mo window is 92 days"),
        ("error" in get_cn_quote("AAPL"), "US ticker rejected by get_cn_quote"),
        ("error" in get_cn_history("600519.SS", "max"), "period 'max' rejected"),
        ("error" in get_hk_quote("600519.SS"), "A-share rejected by get_hk_quote"),
    )
    passed = 0
    for hit, label in cases:
        passed += hit
        print(f"  {'ok ' if hit else 'XX '} {label}")
    return passed, len(cases)


def _self_test() -> int:
    """Deterministic built-in tests: no network, no clock, no randomness."""
    results = [_run_accept_cases(), _run_reject_cases(), _run_derived_cases()]
    passed = sum(p for p, _ in results)
    total = sum(t for _, t in results)
    print(f"\n{passed}/{total} akshare_mcp unit tests passed.")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AkShare MCP server (A-share / HK / index). No API key required.",
    )
    parser.add_argument("--self-test", action="store_true",
                        help="run deterministic built-in unit tests and exit")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()
    if mcp is None:
        print("mcp SDK not installed; run via `uv run --no-project --quiet --script`.",
              file=sys.stderr)
        return 2
    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
