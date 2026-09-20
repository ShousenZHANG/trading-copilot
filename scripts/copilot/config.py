"""User configuration: adopted parameters, never secrets.

Secrets stay in `.env` (see service.KEY_NAMES). This file holds the values a
user *decides* — universe, capital, comfort constraints, notification cadence —
so they are reviewable, versionable locally, and excluded from the release.
TOML via stdlib `tomllib` keeps the offline CI job dependency-free.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from .instruments import DEFENSIVE_ETFS, ETF_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "config" / "user.toml"
SCHEMA_VERSION = 1
MAX_UNIVERSE = 12
_SECTIONS = ("etf", "gold", "notify")
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


@dataclass(frozen=True)
class NotifyConfig:
    email_to: str = ""
    timezone: str = "Australia/Sydney"
    scan_time_local: str = "07:00"
    language: str = "zh"


@dataclass(frozen=True)
class Config:
    present: bool
    path: str
    etf: EtfConfig
    gold: GoldConfig
    notify: NotifyConfig


def _number(section: dict, key: str, prefix: str, *, low: float, high: float,
            integer: bool = False) -> float | int:
    """Reject bools explicitly: `isinstance(True, int)` is True in Python."""
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{prefix}.{key} must be a number")
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
    return GoldConfig(
        investable_total_cny=_number(section, "investable_total_cny", "gold", low=0, high=1e9),
        min_order_cny=_number(section, "min_order_cny", "gold", low=1, high=1e7, integer=True),
        order_increment_cny=_number(section, "order_increment_cny", "gold", low=1, high=1e6, integer=True),
        max_orders_per_day=_number(section, "max_orders_per_day", "gold", low=1, high=100, integer=True),
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
    target = Path(path) if path else DEFAULT_PATH
    if not target.is_file():
        return Config(present=False, path=str(target), etf=EtfConfig(), gold=GoldConfig(),
                      notify=NotifyConfig())
    with target.open("rb") as handle:
        raw = tomllib.load(handle)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}, got {raw.get('schema_version')!r}")
    for key in raw:
        if key != "schema_version" and key not in _SECTIONS:
            raise ValueError(f"unknown section '{key}' — secrets belong in .env, not config")
    for name in _SECTIONS:
        if not isinstance(raw.get(name), dict):
            raise ValueError(f"missing [{name}] section")
    return Config(present=True, path=str(target), etf=_etf(raw["etf"]), gold=_gold(raw["gold"]),
                  notify=_notify(raw["notify"]))


def as_dict(loaded: Config) -> dict:
    return asdict(loaded)
