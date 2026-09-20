"""Rule identity and the adoption record. Pure, stdlib, no I/O.

WHY A NEW IDENTIFIER
A backtest Result carries `rule_name` (the family) and `parameters`. Neither
identifies a strategy: `parameters` deliberately excludes `universe` and
`targets` so the admission gate's three-parameter cap measures only fitted
knobs, which means two runs over completely different baskets report the same
name and parameters.

A rule_id is content-addressed over everything that changes what the strategy
does or what evidence admitted it: the family, its parameters, the universe, any
fixed targets, the cost model the backtest was charged under, the cash floor, the
share granularity, and the admission verdict. The cost model and cash floor are
part of identity on purpose -- a rule sized live under a different fee schedule
or a different reserve has admitted metrics that no longer describe it.

WHY THE VERDICT IS DERIVED, NOT SUPPLIED
`build_adoption` takes a backtest `Result` and calls the admission gate itself.
A caller cannot hand it `{"admitted": True}`: adoption is supposed to mean "this
passed the gate", and a self-asserted boolean would make a hand-written JSON
pipe enough to adopt a strategy that never ran.

FORMAT
`rule-` plus 16 lowercase hex characters. The prefix is tokenised by
policy._IDENTITY_OR_DATE so an engine-authored reason can cite the id without
tripping the numeric-provenance gate.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
#: Only the ETF sleeve exists. ADR-0005 clause 2 deferred gold, and a gold
#: adoption would skip every universe check here while still being reachable
#: through etf.adopted_rule_id, which is the only pointer that exists.
SLEEVES = ("etf",)
ID_PREFIX = "rule-"
ID_HEX_LENGTH = 16

_COST_FIELDS = ("per_share_usd", "minimum_usd", "max_pct_of_notional", "spread_bps")

#: Exactly the keys each family's `parameters` property returns. Validated so a
#: typo cannot hash into the identity while the evaluator reads the correct name
#: and silently falls back to its default.
_FAMILY_PARAMETERS = {
    "fixed_weight_bands": ("relative_band", "absolute_band", "calendar_days"),
    "inverse_volatility": ("lookback_days", "rebalance_days"),
    "momentum_top_n": ("top_n", "lookback_days", "skip_days"),
}
WEIGHT_TOLERANCE = 1e-6


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _clean_cost_model(cost_model: Mapping[str, Any]) -> dict:
    if not isinstance(cost_model, Mapping):
        raise ValueError("cost_model must be a mapping")
    missing = [key for key in _COST_FIELDS if key not in cost_model]
    if missing:
        raise ValueError(f"cost_model is missing {', '.join(missing)}; an incomplete mapping "
                         "would construct the default fee schedule silently and the admitted "
                         "metrics would stop describing this rule")
    cleaned: dict[str, Any] = {}
    for key in _COST_FIELDS:
        value = cost_model[key]
        if key == "max_pct_of_notional" and value is None:
            cleaned[key] = None     # None is uncapped; 0.0 is a real zero cap
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"cost_model.{key} must be a number")
        cleaned[key] = float(value)
    return cleaned


def _clean_universe(universe: Sequence[str]) -> list[str]:
    if not isinstance(universe, (list, tuple)) or not universe:
        raise ValueError("universe must be a nonempty list or tuple of symbols")
    symbols = sorted({str(s).upper() for s in universe})
    if len(symbols) != len(universe):
        raise ValueError("universe contains duplicate symbols")
    return symbols


def _clean_parameters(family: str, parameters: Mapping[str, float]) -> dict:
    expected = _FAMILY_PARAMETERS.get(family)
    if expected is None:
        raise ValueError(f"unknown rule family {family!r}; known families are "
                         f"{tuple(_FAMILY_PARAMETERS)}")
    supplied = dict(parameters or {})
    unknown = sorted(set(supplied) - set(expected))
    if unknown:
        raise ValueError(f"{family} has no parameter(s) {', '.join(unknown)}; expected "
                         f"exactly {expected}")
    missing = [key for key in expected if key not in supplied]
    if missing:
        raise ValueError(f"{family} requires parameter(s) {', '.join(missing)}")
    return {key: float(supplied[key]) for key in expected}


def _clean_parameter_values(parameters: Mapping[str, float]) -> dict:
    """Coerce a parameters mapping to floats for hashing.

    Deliberately family-agnostic: rule_id is a pure hashing primitive, so it
    does not enforce that `parameters`'s keys match `_FAMILY_PARAMETERS[family]`
    -- that stricter, family-aware check is `_clean_parameters`, used only by
    `build_adoption`, which validates a real rule's own parameters before it
    ever calls this function. Two independent axes (family, parameters) must
    each be free to vary on their own for the identity hash to prove each one
    participates.
    """
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters must be a mapping")
    cleaned: dict[str, float] = {}
    for key, value in parameters.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"parameters.{key} must be a number")
        cleaned[str(key)] = float(value)
    return cleaned


def rule_id(*, family: str, parameters: Mapping[str, float], universe: Sequence[str],
            targets: Mapping[str, float] | None, cost_model: Mapping[str, Any],
            cash_floor_pct: float, integer_shares: bool,
            admission: Mapping[str, Any]) -> str:
    """Content-addressed identity. Universe order does not matter."""
    material = {
        "schema_version": SCHEMA_VERSION,
        "family": str(family),
        "parameters": _clean_parameter_values(parameters),
        "universe": _clean_universe(universe),
        "targets": ({str(k): float(v) for k, v in dict(targets).items()} if targets else None),
        "cost_model": _clean_cost_model(cost_model),
        "cash_floor_pct": float(cash_floor_pct),
        "integer_shares": bool(integer_shares),
        "admitted": bool(admission.get("admitted")),
        "waived": bool(admission.get("waived")),
    }
    return ID_PREFIX + hashlib.sha256(_canonical(material).encode()).hexdigest()[:ID_HEX_LENGTH]


def build_adoption(*, sleeve: str, family: str, parameters: Mapping[str, float],
                   universe: Sequence[str], targets: Mapping[str, float] | None,
                   result, cost_model: Mapping[str, Any], cash_floor_pct: float,
                   integer_shares: bool, waivers: Mapping[str, str] | None = None) -> dict:
    """Build the immutable record of adopting a rule. Raises rather than guesses.

    `result` is a backtest engine.Result. The admission verdict is computed here
    from it, never taken from a caller.
    """
    from .backtest import admission as admission_gate
    from .backtest import universe as universe_tiers

    if sleeve not in SLEEVES:
        raise ValueError(f"sleeve must be one of {SLEEVES}, got {sleeve!r}")
    symbols = _clean_universe(universe)
    cleaned_parameters = _clean_parameters(family, parameters)
    for symbol in symbols:
        classification = universe_tiers.classify(symbol)
        if not classification.admissible:
            raise ValueError(f"{symbol} is tier {classification.tier!r}: {classification.reason}")
    cleaned_targets = None
    if family == "fixed_weight_bands":
        if not targets:
            raise ValueError("fixed_weight_bands requires targets")
        cleaned_targets = {str(k).upper(): float(v) for k, v in dict(targets).items()}
        if set(cleaned_targets) != set(symbols):
            raise ValueError(f"targets must cover exactly the universe; targets name "
                             f"{sorted(cleaned_targets)} and the universe is {symbols}")
        total = sum(cleaned_targets.values())
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            raise ValueError(f"targets must sum to 1.0, got {total:.9f}")

    report = admission_gate.assess(result, sessions_by_year=admission_gate.STRESS_SESSIONS,
                                   waivers=dict(waivers or {}))
    if not report.admitted:
        raise ValueError("this backtest was not admitted by the gate, so the rule cannot "
                         "be adopted: " + "; ".join(report.failures))
    admission = {"admitted": True, "waived": bool(report.waived),
                 "waiver_reasons": list(report.waiver_reasons),
                 "failures": list(report.failures), "metrics": dict(report.metrics)}
    identity = rule_id(family=family, parameters=cleaned_parameters, universe=symbols,
                       targets=cleaned_targets, cost_model=cost_model,
                       cash_floor_pct=cash_floor_pct, integer_shares=integer_shares,
                       admission=admission)
    return {
        "schema_version": SCHEMA_VERSION, "rule_id": identity, "sleeve": sleeve,
        "family": str(family), "parameters": cleaned_parameters, "universe": symbols,
        "targets": cleaned_targets, "cost_model": _clean_cost_model(cost_model),
        "cash_floor_pct": float(cash_floor_pct), "integer_shares": bool(integer_shares),
        "admission": admission, "waived": bool(report.waived),
    }
