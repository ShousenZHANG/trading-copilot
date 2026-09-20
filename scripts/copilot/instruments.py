"""Small, explicit registry for the conversation copilot's supported markets.

Scope (design session 2026-09-19): US-listed ETFs from the whitelist below, the
two Nasdaq indexes as benchmarks, and Shanghai Gold Exchange gold in RMB.
Nothing else resolves. An exchange benchmark is never silently converted into a
tradeable product, and an unknown US ticker is rejected rather than
provisionally accepted as a stock.
"""
from __future__ import annotations

import re

_ALIASES = {
    "NASDAQ100": "^NDX", "NASDAQ-100": "^NDX", "NDX": "^NDX",
    "NASDAQ": "^IXIC", "NASDAQ COMPOSITE": "^IXIC", "IXIC": "^IXIC",
    "纳斯达克100": "^NDX", "纳斯达克综合指数": "^IXIC",
    "GOLD": "GOLD.CNY", "黄金": "GOLD.CNY", "实物黄金": "GOLD.CNY",
    "SGE.AU9999": "GOLD.CNY", "AU99.99": "GOLD.CNY", "SHAU": "SGE.SHAU",
}

# The complete tradable universe. config/user.toml picks a subset of this set;
# backtests only accept symbols found here. Adding a symbol is a code change
# with a test, never a runtime decision.
ETF_REGISTRY = frozenset({
    # broad US / global equity
    "SPY", "VOO", "IVV", "SPLG", "VTI", "VT", "DIA", "IWM", "VUG", "VTV", "SCHD",
    "VEA", "VWO", "VXUS", "IOO",
    # Nasdaq-100 family
    "QQQ", "QQQM",
    # covered-call income on the Nasdaq-100 / S&P 500 (proxy-backtested)
    "QQQI", "JEPQ", "JEPI",
    # sectors
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
    "SMH", "SOXX",
    # bonds and gold ETFs stay resolvable for context; the ETF sleeve excludes
    # them by design and config validation enforces that.
    "TLT", "BND", "GLD", "IAU", "SGOL", "GLDM",
})

DEFENSIVE_ETFS = frozenset({"TLT", "BND", "GLD", "IAU", "SGOL", "GLDM"})

_ISSUERS = {
    "QQQ": "https://www.invesco.com/qqq-etf/en/about.html",
    "QQQM": "https://www.invesco.com/us/financial-products/etfs/product-detail?productId=ETF-QQQM",
    "QQQI": "https://neosfunds.com/qqqi/",
    "JEPQ": "https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-nasdaq-equity-premium-income-etf-etf-shares-46654q203",
    "JEPI": "https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-equity-premium-income-etf-etf-shares-46641q332",
    "IOO": "https://www.ishares.com/us/products/239737/ishares-global-100-etf",
}

_BENCHMARKS = frozenset({"GOLD.CNY", "SGE.SHAU", "^NDX", "^IXIC"})


def normalize_instrument(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("instrument must be a non-empty string")
    symbol = _ALIASES.get(value.strip().upper(), value.strip().upper())
    if symbol in _BENCHMARKS:
        return symbol
    # No foreign suffixes, futures, FX, paths, or ambiguous gold spot substitutes.
    # A US class-share shape (BRK-B) is well-formed but still has to be in the
    # registry below, so it is rejected there with the registry's own message.
    if not re.fullmatch(r"[A-Z]{1,6}(?:-[A-Z])?", symbol):
        raise ValueError("unsupported instrument: use a whitelisted US ETF, ^NDX, ^IXIC, or GOLD.CNY")
    if symbol not in ETF_REGISTRY:
        raise ValueError(f"{symbol} is not in the ETF registry; supported ETFs: {', '.join(sorted(ETF_REGISTRY))}")
    return symbol


#: Static sector attribution for the registry. The nine SPDR select-sector funds
#: and the two semiconductor funds are single-sector by construction; everything
#: else is broad and gets "diversified", which is not a sector and therefore
#: never concentrates. This exists so policy's sector limit is computable at all
#: -- no configured source provides fund constituents, so the alternative is a
#: permanently "unknown" check that blocks execution for an invisible reason.
#:
#: Defensive ETFs are included because the map must cover the whole registry;
#: the ETF sleeve itself still refuses them (config rejects DEFENSIVE_ETFS).
#:
#: "diversified" means "no single-sector attribution available for this fund",
#: NOT "measured as diversified". QQQ, QQQM, JEPQ and QQQI all fall through to
#: it and are treated identically to SPY/VOO/IVV, but the Nasdaq-100 is
#: structurally concentrated in mega-cap technology and communications --
#: nothing here says so. They are deliberately not relabelled "technology":
#: QQQ genuinely is more diversified than XLK, so that label would overstate
#: its concentration in the other direction, and the truthful measure is
#: look-through holdings overlap, which no configured source provides here
#: either -- the same root cause ADR-0007 clause 7 records for the disabled
#: correlation check. Consequence: a QQQ-plus-XLK basket reports only XLK's
#: technology weight against the sector limit, while the basket's true
#: technology exposure is materially higher than that number shows.
_SECTORS = {
    "XLK": "technology", "XLF": "financials", "XLE": "energy", "XLV": "health_care",
    "XLY": "consumer_discretionary", "XLP": "consumer_staples", "XLI": "industrials",
    "XLB": "materials", "XLU": "utilities", "XLRE": "real_estate",
    "XLC": "communication_services", "SMH": "technology", "SOXX": "technology",
    "TLT": "government_bonds", "BND": "aggregate_bonds",
    "GLD": "gold", "IAU": "gold", "SGOL": "gold", "GLDM": "gold",
}


def sector_of(value: str) -> str:
    """The sector a registry symbol concentrates in, or "diversified"."""
    symbol = normalize_instrument(value)
    if symbol not in ETF_REGISTRY:
        raise ValueError(f"{symbol} is not in the ETF registry")
    return _SECTORS.get(symbol, "diversified")


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
        "instrument_id": symbol, "asset_class": "index" if index else "etf",
        "currency": "USD", "unit": "point" if index else "share", "tradable": not index,
        "calendar": "XNYS", "timezone": "America/New_York",
        "price_kind": "index_close" if index else "regular_session_close",
        "adjustment": "none" if index else "split",
        "issuer_url": _ISSUERS.get(symbol),
        "identity_status": "registered",
    }
