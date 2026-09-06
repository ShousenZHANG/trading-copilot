"""Small, explicit registry for the conversation copilot's supported markets.

An exchange benchmark is never silently converted into a tradeable product.
Unknown US symbols must subsequently pass the provider's currency/type check.
"""
from __future__ import annotations

import re

_ALIASES = {
    "NASDAQ100": "^NDX", "NASDAQ-100": "^NDX", "NDX": "^NDX",
    "NASDAQ": "^IXIC", "NASDAQ COMPOSITE": "^IXIC", "IXIC": "^IXIC",
    "纳斯达克100": "^NDX", "纳斯达克综合指数": "^IXIC",
    "GOLD": "GOLD.CNY", "黄金": "GOLD.CNY", "实物黄金": "GOLD.CNY",
    "SGE.AU9999": "GOLD.CNY", "AU99.99": "GOLD.CNY", "SHAU": "SGE.SHAU",
    "BRK.B": "BRK-B", "BF.B": "BF-B",
}
_ETF = {"QQQ", "QQQM", "SPY", "VOO", "IVV", "VTI", "VT", "DIA", "IWM",
        "GLD", "IAU", "SGOL", "GLDM", "TLT", "BND", "SCHD", "VUG", "VTV",
        "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU",
        "XLRE", "XLC", "SMH", "SOXX", "SPLG", "VEA", "VWO", "VXUS"}
_ISSUERS = {
    "QQQ": "https://www.invesco.com/qqq-etf/en/about.html",
    "QQQM": "https://www.invesco.com/us/financial-products/etfs/product-detail?productId=ETF-QQQM",
}


def normalize_instrument(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("instrument must be a non-empty string")
    symbol = _ALIASES.get(value.strip().upper(), value.strip().upper())
    if symbol in {"GOLD.CNY", "SGE.SHAU", "^NDX", "^IXIC"}:
        return symbol
    # No foreign suffixes, futures, FX, paths, or ambiguous gold spot substitutes.
    if not re.fullmatch(r"[A-Z]{1,6}(?:-[A-Z])?", symbol):
        raise ValueError("unsupported instrument: use a US stock/ETF, ^NDX, ^IXIC, or GOLD.CNY")
    if symbol in {"CON", "PRN", "AUX", "NUL"}:
        raise ValueError("invalid instrument")
    return symbol


def get_instrument(value: str) -> dict:
    symbol = normalize_instrument(value)
    if symbol in {"GOLD.CNY", "SGE.SHAU"}:
        return {
            "instrument_id": symbol,
            "asset_class": "physical_gold" if symbol == "GOLD.CNY" else "index",
            "currency": "CNY", "unit": "gram", "tradable": False,
            "calendar": "SGE", "timezone": "Asia/Shanghai",
            "price_kind": "sge_au9999_close" if symbol == "GOLD.CNY" else "shau_pm_benchmark",
            "adjustment": "none", "purity": "0.9999",
            "quote_role": "market_benchmark_not_retail_quote",
        }
    index = symbol in {"^NDX", "^IXIC"}
    return {
        "instrument_id": symbol, "asset_class": "index" if index else "etf" if symbol in _ETF else "stock",
        "currency": "USD", "unit": "point" if index else "share", "tradable": not index,
        "calendar": "XNYS", "timezone": "America/New_York",
        "price_kind": "index_close" if index else "regular_session_close",
        "adjustment": "none" if index else "split",
        "issuer_url": _ISSUERS.get(symbol),
        "identity_status": "registered" if index or symbol in _ETF else "requires_provider_confirmation",
    }
