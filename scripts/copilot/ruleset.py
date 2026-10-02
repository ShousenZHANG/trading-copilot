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

WHAT THIS DOES AND DOES NOT GUARANTEE
`build_adoption` takes a backtest `Result` and calls the admission gate itself
rather than accepting a caller's `{"admitted": True}` -- a self-asserted boolean
would make a hand-written JSON pipe enough to adopt a strategy that never ran.
It checks family, parameters, universe, fixed targets, cost model, cash floor
and share granularity against the engine-produced Result, and refuses any
disagreement or absent execution metadata. That check guarantees the
recorded identity and the attached admission metrics always describe the same
backtest: a caller cannot pass a genuinely admitted Result for one
configuration and have it recorded, with that Result's metrics, against a
different one -- the mismatch that a security review demonstrated working
against an earlier version of this module (a `top_n=2` three-symbol Result
adopted, with its own `cagr`, under a recorded identity of `top_n=5` over a
single symbol that never ran).

It does NOT guarantee the Result itself is genuine. Anyone running code in
this process can construct a fabricated `engine.Result` -- a made-up curve,
an invented `total_costs`, whatever `rule_name` and `parameters` they choose
-- and `admission.assess` cannot tell it apart from a real backtest, because
nothing on `Result` carries proof of how it was produced. That is not a gap
specific to this module: anyone who can construct a `Result` in this process
can also write directly to the SQLite journal, so this was never a boundary
`build_adoption` could defend, and claiming otherwise would be a guarantee the
code cannot back. What the binding check above actually closes is the
narrower, real gap: a real, admitted Result silently recorded under a
configuration it never ran.

FORMAT
`rule-` plus 16 lowercase hex characters. The prefix is tokenised by
policy._IDENTITY_OR_DATE so an engine-authored reason can cite the id without
tripping the numeric-provenance gate.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Sequence

# Version 2 binds complete execution settings and uses funded sizing / reserve-
# aware bands. Schema 3 also binds cadence to actual nonzero executions;
# a zero-quantity attempt does not advance the rebalance interval. Earlier
# admission metrics cannot establish the evidence for this calculation.
# The journal retains old records; execution surfaces require this version.
SCHEMA_VERSION = 3
#: The sleeves that may be adopted. Each one brings its own universe check and
#: its own exchange calendar; adding a name here without both is the bug the
#: old "only the ETF sleeve exists" comment warned about, because a sleeve with
#: neither would skip every universe check while remaining reachable through
#: etf.adopted_rule_id. Gold's universe check is `_GOLD_UNIVERSE` below and its
#: calendar is `_stress_table`.
SLEEVES = ("etf", "gold")
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
    "scheduled_accumulation": ("interval_days", "trend_days", "pause_below_trend"),
}
WEIGHT_TOLERANCE = 1e-6

#: The gold sleeve's universe is fixed, not chosen. There is one instrument and
#: `backtest.universe.classify` cannot tier it -- GOLD.CNY is not in
#: ETF_REGISTRY and classify() raises for it -- so without this pin a gold
#: adoption really would skip every universe check, which is precisely what the
#: old SLEEVES comment warned about.
_GOLD_UNIVERSE = ["GOLD.CNY"]


def _stress_table(sleeve: str) -> dict[int, int]:
    """The stress-session counts for this sleeve's own exchange calendar.

    Before this existed, build_adoption passed admission's XNYS table
    unconditionally, so a Shanghai series was judged against New York trading
    days: SGE runs 242 sessions in 2020 against XNYS's 253, so eleven sessions
    gold genuinely never had were counted against it in both directions.
    """
    from .backtest import admission as admission_gate

    return admission_gate.stress_sessions_for(sleeve)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _clean_cost_model(cost_model: Mapping[str, Any]) -> dict:
    if not isinstance(cost_model, Mapping):
        # engine.CostModel is a frozen dataclass carrying exactly _COST_FIELDS,
        # and it is what every caller already holds: it built one, handed it to
        # engine.run, and must now adopt that result under the same schedule.
        # Forcing a hand-written dict at that point invited the drift this
        # function exists to catch -- a fee schedule retyped beside the one the
        # backtest actually charged. Anything else still raises, and a dataclass
        # missing a field still fails the completeness check below.
        if is_dataclass(cost_model) and not isinstance(cost_model, type):
            cost_model = asdict(cost_model)
        else:
            raise ValueError("cost_model must be a mapping or a backtest CostModel")
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


def _largest_position_weight(family: str, parameters: Mapping[str, float],
                             symbols: Sequence[str],
                             targets: Mapping[str, float] | None) -> float | None:
    """The largest share of the invested book one holding can take, or None.

    None means the maximum is data-dependent and cannot be known at adoption
    time. Inverse-volatility weighting is the case: a single unusually calm name
    can take an arbitrarily large share, so the only honest pre-trade bound is
    1.0, and enforcing that would reject every such rule. Those rules can
    therefore still produce a research-only result on a day when one name's
    weight breaches the sleeve limit, and that is visible when it happens.
    """
    if family == "fixed_weight_bands":
        return max(targets.values()) if targets else None
    if family == "momentum_top_n":
        held = min(int(parameters["top_n"]), len(symbols))
        return 1.0 / held if held else None
    if family == "scheduled_accumulation":
        # One instrument at weight 1.0, and the gold sleeve has no single-name
        # cap (policy._SLEEVE_EXEMPT). Applying the ETF check here would compare
        # 1.0 against 0.25 and refuse every gold rule. Written out rather than
        # left to the fall-through so a reader sees this was decided, not
        # forgotten.
        return None
    return None


def _refuse_an_unexecutable_rule(family: str, parameters: Mapping[str, float],
                                 symbols: Sequence[str],
                                 targets: Mapping[str, float] | None,
                                 cash_floor_pct: float) -> None:
    """Refuse a rule whose largest holding can never clear the sleeve's limit.

    A rule that always breaches single-name concentration is not a rule that
    sometimes pauses -- it can never produce an executable order at all, and
    every daily evaluation of it would return research_only forever. Catching
    that here turns a permanent silent failure into one sentence at adoption.

    The arithmetic: N equally weighted holdings with a cash floor f give each
    one (1 - f) / N of the book. Against the ETF sleeve's 25% ceiling that needs
    N >= 4(1 - f) -- so four holdings at the default 15% floor, and a
    twelve-symbol universe does not help a momentum rule that holds only two.
    """
    from .policy import limit_for

    largest = _largest_position_weight(family, parameters, symbols, targets)
    if largest is None:
        return
    limit = limit_for("single_name", "etf")
    worst = (1.0 - cash_floor_pct) * largest
    if worst > limit + WEIGHT_TOLERANCE:
        raise ValueError(
            f"this rule can never produce an executable order: its largest holding would be "
            f"{worst:.1%} of the book, above the {limit:.0%} single-name limit for an ETF "
            f"sleeve. Hold more names, raise the cash floor above "
            f"{1.0 - limit / largest:.0%}, or widen the rule's concentration.")


def build_adoption(*, sleeve: str, result, cost_model: Mapping[str, Any],
                   cash_floor_pct: float, integer_shares: bool,
                   family: str | None = None,
                   parameters: Mapping[str, float] | None = None,
                   universe: Sequence[str] | None = None,
                   targets: Mapping[str, float] | None = None,
                   waivers: Mapping[str, str] | None = None) -> dict:
    """Build the immutable record of adopting a rule. Raises rather than guesses.

    `result` is a backtest engine.Result. The admission verdict is computed here
    from it, never taken from a caller. Before that verdict is even asked for,
    all recorded strategy/execution settings are checked against the inputs
    being adopted -- see the module docstring for exactly
    what that binding does and does not guarantee.

    `family`, `parameters` and `universe` may be omitted, which means "adopt
    exactly the backtest that ran" and reads them off `result`. That is not a
    way around the binding check: the check defends against a real, admitted
    Result being recorded under a configuration it never ran, and a caller who
    states no second configuration has created no such mismatch. A caller
    holding a configuration in hand should still pass it, so the disagreement
    can be caught.
    """
    from .backtest import admission as admission_gate
    from .backtest import goldrules as goldrules_module
    from .backtest import universe as universe_tiers

    if sleeve not in SLEEVES:
        raise ValueError(f"sleeve must be one of {SLEEVES}, got {sleeve!r}")
    family = result.rule_name if family is None else family
    parameters = dict(result.parameters) if parameters is None else parameters
    universe = tuple(result.universe) if universe is None else universe
    symbols = _clean_universe(universe)
    cleaned_parameters = _clean_parameters(family, parameters)
    if sleeve == "gold":
        # Gold's universe is fixed, not chosen, and `universe_tiers.classify`
        # cannot judge it: GOLD.CNY is not in ETF_REGISTRY, so classify() would
        # raise "not in the ETF registry" for the only symbol this sleeve has.
        # Pinning it here is the gold sleeve's universe check -- without one,
        # a gold adoption would carry any basket a caller named.
        if symbols != _GOLD_UNIVERSE:
            raise ValueError(f"the gold sleeve trades exactly {_GOLD_UNIVERSE}, not "
                             f"{symbols}; there is no other instrument in it")
    else:
        for symbol in symbols:
            classification = universe_tiers.classify(symbol)
            if not classification.admissible:
                raise ValueError(f"{symbol} is tier {classification.tier!r}: "
                                 f"{classification.reason}")
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

    # Bind the Result to the configuration being adopted, before it is ever
    # handed to the admission gate. Without this, a genuinely admitted Result
    # for one family/parameters/universe could be recorded -- with its own
    # admitted metrics -- against a completely different one; a security
    # review demonstrated exactly that against an earlier version of this
    # function. See the module docstring for what this check does and does
    # not guarantee.
    if result.rule_name != family:
        raise ValueError(f"result.rule_name {result.rule_name!r} does not match family "
                         f"{family!r}; the admitted backtest must describe the strategy "
                         "being adopted")
    if result.parameters != cleaned_parameters:
        raise ValueError(f"result.parameters {result.parameters!r} does not match the "
                         f"adopted parameters {cleaned_parameters!r}; the admitted backtest "
                         "must describe the strategy being adopted")
    if sorted(result.universe) != symbols:
        raise ValueError(f"result.universe {sorted(result.universe)!r} does not match the "
                         f"adopted universe {symbols!r}; the admitted backtest must describe "
                         "the strategy being adopted")

    actual_targets = getattr(result, "targets", None)
    if actual_targets != cleaned_targets:
        raise ValueError("adopted targets do not match the actual backtest targets")
    actual_costs = getattr(result, "cost_model", None)
    if actual_costs is None or _clean_cost_model(actual_costs) != _clean_cost_model(cost_model):
        raise ValueError("adopted cost_model does not match the actual backtest cost_model")
    if getattr(result, "cash_floor_pct", None) != float(cash_floor_pct):
        raise ValueError("adopted cash_floor_pct does not match the actual backtest cash_floor_pct")
    if type(integer_shares) is not bool or getattr(result, "integer_shares", None) is not integer_shares:
        raise ValueError("adopted integer_shares does not match the actual backtest integer_shares")
    expected_execution = {"execution_price": "same_bar_close",
                          "signal_cutoff": "includes_current_bar",
                          "rebalance_cadence": "completed_nonzero_trades"}
    actual_execution = getattr(result, "execution_assumptions", None)
    if (not isinstance(actual_execution, dict)
            or any(actual_execution.get(key) != value for key, value in expected_execution.items())):
        raise ValueError("execution evidence does not match the current adoptable backtest; "
                         "rerun it with the current engine before adoption")

    _refuse_an_unexecutable_rule(family, cleaned_parameters, symbols, cleaned_targets,
                                 float(cash_floor_pct))

    report = admission_gate.assess(result, sessions_by_year=_stress_table(sleeve),
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
    adoption = {
        "schema_version": SCHEMA_VERSION, "rule_id": identity, "sleeve": sleeve,
        "family": str(family), "parameters": cleaned_parameters, "universe": symbols,
        "targets": cleaned_targets, "cost_model": _clean_cost_model(cost_model),
        "cash_floor_pct": float(cash_floor_pct), "integer_shares": bool(integer_shares),
        "admission": admission, "waived": bool(report.waived),
        "execution_assumptions": dict(getattr(result, "execution_assumptions", {})),
    }
    if sleeve == "gold":
        if float(result.parameters.get("pause_below_trend", 0.0)):
            raise ValueError(
                "pause_below_trend cannot be adopted: at a one-instrument universe "
                "engine.run rebalances to weight 1.0, so every veto the filter raises "
                "trades zero and the backtest cannot distinguish the filter from buy "
                "and hold. Adopting it would route an untested signal into live order "
                "sizing. Measured 2026-09-20: interval_days 21 and 252 differ by 3 CNY "
                "in 360,000. Lift this by teaching engine.run periodic contributions, "
                "in its own plan, with its own bt cross-check.")
        # brake.DISCLOSURE's precedent: an unbacktested input says so where the
        # number is read, not in a document the reader may never open. The
        # schedule is such an input -- see goldrules.DISCLOSURE for the
        # measurement -- so it travels on the adoption record itself.
        adoption["schedule_disclosure"] = goldrules_module.DISCLOSURE
    return adoption
