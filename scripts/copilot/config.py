"""User configuration: adopted parameters, never secrets.

Secrets stay in `.env` (see service.KEY_NAMES). This file holds the values a
user *decides* — universe, capital, comfort constraints, notification cadence —
so they are reviewable, versionable locally, and excluded from the release.
TOML via stdlib `tomllib` keeps the offline CI job dependency-free.
"""
from __future__ import annotations

import re
import math
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .instruments import DEFENSIVE_ETFS, ETF_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "config" / "user.toml"
SCHEMA_VERSION = 1
MAX_UNIVERSE = 12
_SECTIONS = ("etf", "gold", "notify")
_OPTIONAL_SECTIONS = ("advisor", "ibkr")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_RULE_ID_RE = re.compile(r"^rule-[0-9a-f]{16}$")


@dataclass(frozen=True)
class EtfConfig:
    universe: tuple[str, ...] = ()
    # CASH available to deploy into this sleeve, not its market value -- the
    # same quantity engine.run(start_cash=...) expects (ADR-0007 clause 3).
    investable_cash_usd: float = 0.0
    min_cash_reserve_pct: float = 0.15
    max_drawdown_pct: float = 0.20
    adopted_rule_id: str = ""


@dataclass(frozen=True)
class GoldConfig:
    investable_total_cny: float = 0.0
    min_order_cny: int = 1200
    order_increment_cny: int = 200
    max_orders_per_day: int = 10
    # What one scheduled contribution spends. The rule decides WHEN to add; the
    # engine cannot decide how much, because a contribution schedule is not
    # something engine.run can exercise (see the measurement above Task 5 of the
    # gold-sleeve plan), so this is the user's figure, set by hand like
    # etf.investable_cash_usd. 0 means "size nothing", which goldsizing reports
    # as an ordinary refusal rather than an error.
    contribution_cny: float = 0.0
    # The gold sleeve's own pointer, mirroring etf.adopted_rule_id. Without it
    # `--sleeve gold` had no rule to run and the flag would have been a facade
    # over nothing; pointing the ETF pointer at a gold adoption is refused in
    # service.evaluate, because the two books are separate currencies.
    adopted_rule_id: str = ""


@dataclass(frozen=True)
class NotifyConfig:
    email_to: str = ""
    timezone: str = "Australia/Sydney"
    scan_time_local: str = "07:00"
    language: str = "zh"


@dataclass(frozen=True)
class AdvisorConfig:
    # Research is available without activating any strategy or broker access.
    enabled: bool = True
    research_universe: tuple[str, ...] = ("QQQI", "JEPQ", "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META")
    benchmark: str = "^NDX"
    long_term_rule_id: str = ""
    swing_rule_id: str = ""
    cash_floor_pct: float = 0.05
    swing_nav_cap_pct: float = 0.10
    long_term_nav_cap_pct: float = 0.90
    max_swing_loss_pct: float = 0.005
    max_drawdown_pct: float = 0.10
    max_single_name_pct: float = 0.25
    max_sector_pct: float = 0.50
    max_trade_notional_pct: float = 0.10
    max_spread_pct: float = 0.005
    max_fee_pct: float = 0.01
    quote_ttl_seconds: int = 60
    regular_hours_only: bool = True
    allow_borrowing: bool = False
    fee_model_confirmed: bool = False
    commission_per_share_usd: float = 0.0
    min_commission_usd: float = 0.0
    other_cost_bps: float = 0.0


@dataclass(frozen=True)
class IbkrConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 71
    account_id: str = ""
    account_alias: str = "ibkr"
    paper: bool = True
    timeout_seconds: int = 15
    quote_ttl_seconds: int = 60
    regular_hours_only: bool = True
    orders_scope_confirmed: bool = False
    market_data_feed: str = "unknown"


@dataclass(frozen=True)
class Config:
    present: bool
    path: str
    etf: EtfConfig
    gold: GoldConfig
    notify: NotifyConfig
    advisor: AdvisorConfig = field(default_factory=AdvisorConfig)
    ibkr: IbkrConfig = field(default_factory=IbkrConfig)


def _number(section: dict, key: str, prefix: str, *, low: float, high: float,
            integer: bool = False) -> float | int:
    """Reject bools explicitly: `isinstance(True, int)` is True in Python."""
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{prefix}.{key} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{prefix}.{key} must be finite")
    if integer and int(value) != value:
        raise ValueError(f"{prefix}.{key} must be an integer")
    if not (low <= value <= high):
        raise ValueError(f"{prefix}.{key} must be between {low} and {high}, got {value}")
    return int(value) if integer else float(value)


def _string(section: dict, key: str, prefix: str, *, pattern: re.Pattern | None = None,
            choices: tuple[str, ...] | None = None) -> str:
    value = section.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{prefix}.{key} must be a string")
    if pattern and not pattern.match(value):
        raise ValueError(f"{prefix}.{key} must match {pattern.pattern}, got {value!r}")
    if choices and value not in choices:
        raise ValueError(f"{prefix}.{key} must be one of {choices}, got {value!r}")
    return value


def _etf(section: dict) -> EtfConfig:
    universe = section.get("universe", [])
    if not isinstance(universe, list) or not all(isinstance(s, str) for s in universe):
        raise ValueError("etf.universe must be a list of ticker strings")
    symbols = tuple(s.upper() for s in universe)
    if len(symbols) > MAX_UNIVERSE:
        raise ValueError(f"etf.universe holds at most {MAX_UNIVERSE} symbols, got {len(symbols)}")
    duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
    if duplicates:
        raise ValueError(f"etf.universe contains duplicate symbols: {', '.join(duplicates)}")
    for symbol in symbols:
        if symbol not in ETF_REGISTRY:
            raise ValueError(f"etf.universe: {symbol} is not in the ETF registry")
        if symbol in DEFENSIVE_ETFS:
            raise ValueError(f"etf.universe: {symbol} is a defensive (bond/gold) ETF; "
                             f"the ETF sleeve holds equity ETFs and cash only")
    if "investable_total_usd" in section:
        raise ValueError("investable_total_usd was renamed investable_cash_usd: the value is "
                         "investable CASH available to deploy, not the sleeve's market value "
                         "(ADR-0007 clause 3)")
    adopted = _string(section, "adopted_rule_id", "etf")
    if adopted and not _RULE_ID_RE.match(adopted):
        raise ValueError(f"etf.adopted_rule_id must be empty or look like "
                         f"rule-<16 lowercase hex characters>, got {adopted!r}")
    return EtfConfig(
        universe=symbols,
        investable_cash_usd=_number(section, "investable_cash_usd", "etf", low=0, high=1e9),
        min_cash_reserve_pct=_number(section, "min_cash_reserve_pct", "etf", low=0, high=0.9),
        max_drawdown_pct=_number(section, "max_drawdown_pct", "etf", low=0.01, high=0.9),
        adopted_rule_id=adopted,
    )


def _gold(section: dict) -> GoldConfig:
    # The two keys below are read with a default rather than demanded, because
    # every config/user.toml written before the gold sleeve existed has a
    # [gold] section without them. Requiring them would turn a routine upgrade
    # into "missing gold.contribution_cny" on the next command.
    adopted = section.get("adopted_rule_id", "")
    if not isinstance(adopted, str):
        raise ValueError("gold.adopted_rule_id must be a string")
    if adopted and not _RULE_ID_RE.match(adopted):
        raise ValueError(f"gold.adopted_rule_id must be empty or look like "
                         f"rule-<16 lowercase hex characters>, got {adopted!r}")
    return GoldConfig(
        investable_total_cny=_number(section, "investable_total_cny", "gold", low=0, high=1e9),
        min_order_cny=_number(section, "min_order_cny", "gold", low=1, high=1e7, integer=True),
        order_increment_cny=_number(section, "order_increment_cny", "gold", low=1, high=1e6, integer=True),
        max_orders_per_day=_number(section, "max_orders_per_day", "gold", low=1, high=100, integer=True),
        contribution_cny=_number({**section, "contribution_cny": section.get("contribution_cny", 0)},
                                 "contribution_cny", "gold", low=0, high=1e7),
        adopted_rule_id=adopted,
    )


def _notify(section: dict) -> NotifyConfig:
    return NotifyConfig(
        email_to=_string(section, "email_to", "notify"),
        timezone=_string(section, "timezone", "notify"),
        scan_time_local=_string(section, "scan_time_local", "notify", pattern=_TIME_RE),
        language=_string(section, "language", "notify", choices=("zh", "en")),
    )


def load_config(path: str | Path | None = None) -> Config:
    """Return validated config. A missing file is not an error: defaults, `present=False`."""
    target = Path(path or os.environ.get("COPILOT_CONFIG_PATH") or DEFAULT_PATH)
    if not target.is_file():
        return Config(present=False, path=str(target), etf=EtfConfig(), gold=GoldConfig(),
                      notify=NotifyConfig())
    with target.open("rb") as handle:
        raw = tomllib.load(handle)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}, got {raw.get('schema_version')!r}")
    for key in raw:
        if key != "schema_version" and key not in _SECTIONS + _OPTIONAL_SECTIONS:
            raise ValueError(f"unknown section '{key}' — secrets belong in .env, not config")
    for name in _SECTIONS:
        if not isinstance(raw.get(name), dict):
            raise ValueError(f"missing [{name}] section")
    return Config(present=True, path=str(target), etf=_etf(raw["etf"]), gold=_gold(raw["gold"]),
                  notify=_notify(raw["notify"]), advisor=_optional(raw.get("advisor", {}), AdvisorConfig, "advisor"),
                  ibkr=_optional(raw.get("ibkr", {}), IbkrConfig, "ibkr"))


def _optional(section: dict, cls, prefix: str):
    """Additive sections preserve old configs while rejecting accidental permissive types."""
    if not isinstance(section, dict):
        raise ValueError(f"[{prefix}] must be a table")
    defaults = asdict(cls())
    unknown = set(section) - set(defaults)
    if unknown:
        raise ValueError(f"unknown {prefix} field: {sorted(unknown)[0]}")
    values = {**defaults, **section}
    for name, default in defaults.items():
        value = values[name]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{prefix}.{name} must be boolean")
        elif isinstance(default, (int, float)):
            if prefix == "ibkr" or name == "quote_ttl_seconds":
                bounds = {"port": (1, 65535), "client_id": (1, 2147483647),
                          "timeout_seconds": (1, 30), "quote_ttl_seconds": (1, 120)}
                low, high = bounds[name]
            else:
                low, high = (0, 1) if name.endswith("_pct") else (0, 1000)
            values[name] = _number(values, name, prefix, low=low, high=high,
                                   integer=isinstance(default, int))
        elif isinstance(default, tuple):
            if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{prefix}.{name} must be ticker strings")
            if not 1 <= len(value) <= 24:
                raise ValueError(f"{prefix}.{name} must contain 1 to 24 tickers")
            symbols = tuple(item.strip().upper() for item in value)
            if len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s) for s in symbols):
                raise ValueError(f"{prefix}.{name} contains duplicate/invalid tickers")
            values[name] = symbols
        elif not isinstance(value, str):
            raise ValueError(f"{prefix}.{name} must be a string")
        if name.endswith("rule_id") and value and not _RULE_ID_RE.fullmatch(value):
            raise ValueError(f"{prefix}.{name} must be an adopted rule id")
    if prefix == "advisor":
        if values["benchmark"] not in {"^NDX", "^IXIC", "SPY", "QQQ"}:
            raise ValueError("advisor.benchmark must be an explicitly supported market benchmark")
        if values["allow_borrowing"]:
            raise ValueError("advisor.allow_borrowing is unsupported; manual advisor is cash-only")
    elif values["host"] not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ibkr.host must be local loopback")
    if prefix == "ibkr" and values["market_data_feed"] not in {"unknown", "consolidated", "non_consolidated"}:
        raise ValueError("ibkr.market_data_feed must specify a supported declared feed scope")
    return cls(**values)


def as_dict(loaded: Config) -> dict:
    return asdict(loaded)
