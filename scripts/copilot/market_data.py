"""One immutable, deterministic evidence snapshot for a conversation decision.

The network boundary is injectable. A provider implements supports(instrument)
and fetch(instrument, start_date, end_exclusive, decision_at), returning the
documented dicts in providers.py. Calendars implement window/is_session/close.
No failed refresh is replaced with an old successful snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from datetime import date, timedelta
from pathlib import Path

from .data_calendar import CalendarUnavailable, MarketCalendar, iso, parse_time, utc_now
from .instruments import get_instrument, normalize_instrument
from .providers import AlpacaProvider, HttpClient, NasdaqEquityProvider, NasdaqIndexProvider, ProviderError, SGEProvider, YahooProvider, finite_number


def snapshot_digest(snapshot: dict) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_id"}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "snap_" + hashlib.sha256(encoded).hexdigest()


def verify_snapshot(snapshot: dict) -> bool:
    try:
        return isinstance(snapshot, dict) and snapshot.get("snapshot_id") == snapshot_digest(snapshot)
    except (TypeError, ValueError, OverflowError):
        return False


def _evidence_id(evidence: dict) -> str:
    return "ev_" + hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()[:24]


def compute_indicators(bars: list[dict], basis: str = "split_adjusted") -> dict:
    """SMA, Wilder RSI/ATR, and close-to-close returns, all on one labelled basis.

    Total-return-adjusted OHLC is constructed with each row's Adj Close/Close
    ratio; it is never combined with raw highs/lows for ATR. The 52-week range
    uses 252 exchange observations, not a truncated 3-month window.
    """
    adjusted = basis == "total_return_adjusted"
    closes = [finite_number(bar["adjusted_close"] if adjusted else bar["close"], positive=True) for bar in bars]
    count = len(closes)
    result = {"basis": basis, "sample_count": count, "formula_version": "wilder-v1",
              "sma20": None, "sma50": None, "sma200": None, "rsi14": None,
              "atr14": None, "return_20_sessions": None, "return_252_sessions": None,
              "high_252_sessions": None, "low_252_sessions": None,
              "average_volume_20_sessions": None, "missing": []}
    for period in (20, 50, 200):
        if count >= period:
            result[f"sma{period}"] = sum(closes[-period:]) / period
        else:
            result["missing"].append(f"sma{period}: need {period} valid bars")
    if count >= 15:
        moves = [closes[i] - closes[i - 1] for i in range(1, count)]
        gain = sum(max(change, 0) for change in moves[:14]) / 14
        loss = sum(max(-change, 0) for change in moves[:14]) / 14
        for change in moves[14:]:
            gain = (gain * 13 + max(change, 0)) / 14
            loss = (loss * 13 + max(-change, 0)) / 14
        result["rsi14"] = 50.0 if gain == loss == 0 else 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    else:
        result["missing"].append("rsi14: need 15 valid bars")
    has_ohlc = bool(bars) and all(all(key in bar for key in ("open", "high", "low")) for bar in bars)
    if count >= 15 and has_ohlc:
        ranges = []
        for index in range(1, count):
            factor = closes[index] / bars[index]["close"] if adjusted else 1.0
            high, low = bars[index]["high"] * factor, bars[index]["low"] * factor
            ranges.append(max(high - low, abs(high - closes[index - 1]), abs(low - closes[index - 1])))
        atr = sum(ranges[:14]) / 14
        for value in ranges[14:]:
            atr = (atr * 13 + value) / 14
        result["atr14"] = atr
    else:
        result["missing"].append("atr14: need 15 bars with comparable OHLC")
    for period in (20, 252):
        if count > period:
            result[f"return_{period}_sessions"] = closes[-1] / closes[-period - 1] - 1
        else:
            result["missing"].append(f"return_{period}_sessions: need {period + 1} valid bars")
    if count >= 252:
        if has_ohlc:
            high_low = [(bar["high"] * (bar["adjusted_close"] / bar["close"] if adjusted else 1),
                         bar["low"] * (bar["adjusted_close"] / bar["close"] if adjusted else 1)) for bar in bars[-252:]]
            result["high_252_sessions"] = max(item[0] for item in high_low)
            result["low_252_sessions"] = min(item[1] for item in high_low)
        else:
            result["high_252_sessions"], result["low_252_sessions"] = max(closes[-252:]), min(closes[-252:])
            result["range_scope"] = "benchmark_observations_only"
    else:
        result["missing"].append("52-week range: need 252 valid bars")
    if count >= 20 and all("volume" in bar for bar in bars[-20:]):
        result["average_volume_20_sessions"] = sum(bar["volume"] for bar in bars[-20:]) / 20
        result["volume_unit"] = bars[-1].get("volume_unit", "shares")
    if any(isinstance(value, (int, float)) and not math.isfinite(value) for value in result.values()):
        raise ProviderError("malformed", "indicator arithmetic overflow")
    return result


def _validate_source(source: dict, instrument: dict, expected: str, decision_at, calendar) -> dict:
    source = copy.deepcopy(source)
    current_close_only = (source.get("provider") == "nasdaq" and source.get("upstream") == "Nasdaq US market data"
                          and source.get("adjustment") == "unadjusted_latest_session"
                          and isinstance(source.get("bars"), list) and len(source["bars"]) == 1
                          and source["bars"][0].get("session") == expected and instrument["adjustment"] == "split")
    for field in ("instrument_id", "currency", "unit", "price_kind", "adjustment"):
        if field == "adjustment" and current_close_only:
            continue
        if source.get(field) != instrument[field]:
            raise ProviderError("malformed", f"source {field} does not match requested instrument semantics")
    if source.get("status") != "ok" or not source.get("provider") or not source.get("upstream") or not source.get("source_url", "").startswith("https://"):
        raise ProviderError("malformed", "source lacks success status or provenance")
    parse_time(source["retrieved_at"])
    for field in ("published_at", "available_at"):
        if source.get(field) is not None and parse_time(source[field]) > decision_at:
            raise ProviderError("malformed", "source contains evidence not yet available at decision time")
    if not isinstance(source.get("bars"), list) or not source["bars"]:
        raise ProviderError("not_covered", "source returned no bars")
    records, excluded = {}, []
    for bar in source["bars"]:
        session = date.fromisoformat(bar["session"]).isoformat()
        if not calendar.is_session(instrument, session):
            raise ProviderError("malformed", "bar is dated on a non-trading session")
        close_at = calendar.close(instrument, session)
        if session > expected:
            if close_at > decision_at and close_at.date() <= decision_at.date() + timedelta(days=1):
                excluded.append(session)
                continue
            raise ProviderError("malformed", "future or unfinalized bar outside requested window")
        if close_at > decision_at:
            raise ProviderError("malformed", "unclosed bar included in daily history")
        numeric = {"open", "high", "low", "close", "volume", "adjusted_close", "dividends", "splits", "weighted_average"}
        for key in numeric & bar.keys():
            bar[key] = finite_number(bar[key], positive=key in {"open", "high", "low", "close", "adjusted_close", "weighted_average"})
        if "close" not in bar:
            raise ProviderError("malformed", "bar has no close")
        if "volume" in bar and bar["volume"] < 0:
            raise ProviderError("malformed", "bar volume is negative")
        if all(key in bar for key in ("open", "high", "low")):
            if bar["low"] > min(bar["open"], bar["close"]) + 1e-7 or bar["high"] + 1e-7 < max(bar["open"], bar["close"]) or bar["low"] > bar["high"]:
                raise ProviderError("malformed", "OHLC range is inconsistent")
        if source.get("indicator_basis") == "total_return_adjusted" and "adjusted_close" not in bar:
            raise ProviderError("malformed", "adjusted history missing a row; raw substitution forbidden")
        if session in records and records[session] != bar:
            raise ProviderError("malformed", "source contains conflicting duplicate session bars")
        records[session] = bar
    if not records:
        raise ProviderError("not_covered", "no completed daily bars")
    source["bars"] = [records[session] for session in sorted(records)]
    source["latest_session"] = source["bars"][-1]["session"]
    source["observed_at"] = iso(calendar.close(instrument, source["latest_session"]))
    source["observed_at_semantics"] = "scheduled_session_close; exact publication time unknown"
    if instrument["instrument_id"] == "SGE.SHAU":
        # The published table labels the fixing PM but supplies no exact time.
        # 15:30 is a calendar cutoff, not the benchmark's fixing timestamp.
        source["observed_at"] = None
        source["observation_date"] = source["latest_session"]
        source["observed_at_semantics"] = "SGE PM benchmark date; exact fixing time not supplied"
    source["available_at"] = source.get("available_at")
    source["published_at"] = source.get("published_at")
    source["excluded_incomplete_sessions"] = excluded
    if source["latest_session"] != expected:
        raise ProviderError("stale", f"latest source session {source['latest_session']} differs from expected {expected}")
    # A missing trading day distorts the period of a moving average. An explicit
    # suspension requires separate evidence; do not silently count around gaps.
    cursor = date.fromisoformat(source["bars"][0]["session"])
    missing = []
    while cursor <= date.fromisoformat(expected):
        value = cursor.isoformat()
        if calendar.is_session(instrument, value) and value not in records:
            missing.append(value)
        cursor += timedelta(days=1)
    source["missing_sessions"] = missing
    return source


def _public_evidence(source: dict) -> dict:
    evidence = {key: value for key, value in source.items() if key != "authoritative"}
    evidence["history_sha256"] = hashlib.sha256(json.dumps(source["bars"], sort_keys=True,
                                                           allow_nan=False).encode()).hexdigest()
    evidence["evidence_id"] = _evidence_id(evidence)
    return evidence


def collect_snapshot(instrument_ids: list[str], decision_at: str | None = None, horizon: str = "daily",
                     *, providers: list | None = None, calendar=None, clock=utc_now,
                     cache_dir: str | Path | None = None, minimum_history: int = 260,
                     sge_history_bars: int = 280, close_tolerance: float = 0.001,
                     max_collection_seconds: float = 110, time_fn=time.monotonic) -> dict:
    """Collect current data. Inject providers/calendar/clock for offline contracts.

    Historical date queries are rejected: today's revised Yahoo/SGE history does
    not prove what was available to a past decision. Replay archived snapshots
    for historical evaluation. Comparison tolerance is 10 bps OR two cents;
    it validates closes only and is recorded for later live calibration.
    """
    if not isinstance(instrument_ids, list) or not instrument_ids or len(instrument_ids) > 16:
        raise ValueError("instrument_ids must contain 1 to 16 symbols")
    if not math.isfinite(max_collection_seconds) or not 0 < max_collection_seconds <= 120:
        raise ValueError("max_collection_seconds must be positive and at most 120")
    budget_started = time_fn()
    deadline = budget_started + max_collection_seconds
    if horizon not in {"daily", "swing", "long_term", "long-term", "accumulation"}:
        raise ValueError("only daily, swing, long_term, or accumulation research is supported")
    if minimum_history < 260 or minimum_history > 400:
        raise ValueError("minimum_history must be between 260 and 400 complete sessions")
    if not math.isfinite(close_tolerance) or not 0 < close_tolerance <= 0.005:
        raise ValueError("close_tolerance must be positive and at most 0.005")
    started = parse_time(clock())
    decision = parse_time(decision_at) if decision_at else started
    if decision > started + timedelta(seconds=5) or (started - decision).total_seconds() > 300:
        raise ValueError("historical/future decision_at requires an archived point-in-time snapshot")
    calendar = calendar or MarketCalendar()
    if providers is None:
        http = HttpClient(cache_dir, clock=clock, deadline=deadline, time_fn=time_fn)
        providers = [YahooProvider(http), AlpacaProvider(http), NasdaqIndexProvider(http), NasdaqEquityProvider(http), SGEProvider(http, history_bars=sge_history_bars)]
    canonical = list(dict.fromkeys(normalize_instrument(value) for value in instrument_ids))
    snapshot = {"schema_version": 1, "snapshot_id": "", "created_at": iso(started),
                "decision_at": iso(decision), "valid_until": iso(decision + timedelta(hours=12)),
                "horizon": horizon, "status": "blocked", "instruments": {}, "evidence": [], "issues": [],
                "capabilities": [], "quality_policy": {"minimum_us_history": minimum_history,
                    "close_relative_tolerance": close_tolerance, "close_absolute_tolerance": 0.02,
                    "single_yahoo_is_verified": False, "calendar_delay_minutes": 30,
                    "news_and_fundamentals": "separate_evidence_required"}}
    for symbol in canonical:
        instrument = get_instrument(symbol)
        item = {**instrument, "quality_status": "fail", "latest_session": None,
                "expected_session": None, "price": None, "indicators": {},
                "evidence_ids": [], "issues": [], "sources": []}
        snapshot["instruments"][symbol] = item
        if time_fn() >= deadline:
            item["quality_status"] = "unknown"
            item["issues"].append("collection_deadline_exceeded; current data not collected")
            continue
        try:
            expected, valid_until = calendar.window(instrument, decision)
            item["expected_session"] = expected
            snapshot["valid_until"] = min(snapshot["valid_until"], iso(valid_until))
            if hasattr(calendar, "metadata"):
                item["calendar_evidence"] = calendar.metadata(instrument, decision)
        except (CalendarUnavailable, ImportError, ValueError, KeyError) as exc:
            item["issues"].append("calendar_unavailable: " + str(exc))
            continue
        start = (date.fromisoformat(expected) - timedelta(days=600)).isoformat()
        end = (date.fromisoformat(expected) + timedelta(days=1)).isoformat()
        successes = []
        for provider in providers:
            if hasattr(provider, "supports") and not provider.supports(instrument):
                continue
            name = getattr(provider, "name", type(provider).__name__)
            try:
                if time_fn() >= deadline:
                    raise ProviderError("deadline_exceeded", "shared collection time budget exhausted")
                fetched = provider.fetch(instrument, start, end, decision)
                if time_fn() >= deadline:
                    raise ProviderError("deadline_exceeded", "provider completed after the shared collection deadline")
                source = _validate_source(fetched, instrument, expected, decision, calendar)
                # Metadata-confirmed ETFs outside the small static registry must
                # route later providers and research as ETFs, never company facts.
                source_class = source.get("asset_class")
                if source_class in {"stock", "etf"} and instrument["asset_class"] in {"stock", "etf"}:
                    if instrument.get("identity_status") in {"registered", "provider_confirmed"} and source_class != instrument["asset_class"]:
                        raise ProviderError("malformed", "providers disagree on confirmed stock/ETF identity")
                    instrument["asset_class"] = source_class
                    instrument["identity_status"] = "provider_confirmed"
                    item["asset_class"], item["identity_status"] = source_class, "provider_confirmed"
                evidence = _public_evidence(source)
                snapshot["evidence"].append(evidence)
                item["evidence_ids"].append(evidence["evidence_id"])
                item["sources"].append({"provider": name, "upstream": source["upstream"], "status": "ok",
                                        "source_url": source["source_url"], "evidence_id": evidence["evidence_id"]})
                successes.append(source)
                snapshot["capabilities"].append({"provider": name, "instrument_id": symbol, "status": "available",
                                                 "feed": source.get("feed"), "tested_at": source["retrieved_at"]})
            except (ProviderError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
                status = exc.status if isinstance(exc, ProviderError) else "malformed"
                message = str(exc) if isinstance(exc, ProviderError) else f"{name} response violates data contract"
                item["sources"].append({"provider": name, "status": status, "message": message})
                item["issues"].append(f"{name}: {status}")
                snapshot["capabilities"].append({"provider": name, "instrument_id": symbol, "status": status,
                                                 "tested_at": iso(clock())})
        if not successes:
            if any(source.get("status") == "deadline_exceeded" for source in item["sources"]):
                item["quality_status"] = "unknown"
            item["issues"].append("no_verified_current_price")
            continue
        # Prefer a complete adjusted Yahoo series for indicators. No bar-level
        # merges across providers or refreshes: every revision gets a new hash.
        primary = max(successes, key=lambda s: (not bool(s.get("missing_sessions")),
                       len(s["bars"]) >= minimum_history, s.get("indicator_basis") == "total_return_adjusted", len(s["bars"])))
        item["price"] = primary["bars"][-1]["close"]
        item["latest_session"] = primary["latest_session"]
        item["asset_class"] = primary.get("asset_class", item["asset_class"])
        try:
            item["indicators"] = compute_indicators(primary["bars"], primary.get("indicator_basis", "split_adjusted"))
        except (ProviderError, ValueError, OverflowError, ZeroDivisionError):
            item["issues"].append("indicator_arithmetic_invalid")
            item["quality_status"] = "fail"
            continue
        item["quality_status"] = "unknown"
        item["quality_scope"] = "completed_session_close; internally_validated_history; deterministic_indicators"
        item["verification"] = {"primary_provider": primary["provider"],
                                "history": "primary_source_checked_for_identity_dates_gaps_numeric_validity",
                                "indicators": "deterministic_from_one_primary_series",
                                "cross_provider_ohlcv": False}
        authoritative = primary.get("authoritative") is True and (
            (primary.get("provider") == "sge" and primary.get("upstream") == "Shanghai Gold Exchange") or
            (primary.get("provider") == "nasdaq" and primary.get("upstream") == "Nasdaq index publisher" and instrument["asset_class"] == "index"))
        upstreams = {source["upstream"] for source in successes}
        conflict = False
        comparisons = []
        for source in successes:
            if source is primary or source["upstream"] == primary["upstream"]:
                continue
            primary_by_day = {bar["session"]: bar for bar in primary["bars"]}
            # Compare recent aligned CLOSE observations only, avoiding false
            # volume/OHLC confirmation across consolidated aggregation rules.
            matched = [bar for bar in source["bars"][-5:] if bar["session"] in primary_by_day]
            for bar in matched:
                reference = primary_by_day[bar["session"]]["close"]
                if source["adjustment"] != primary["adjustment"]:
                    split = primary_by_day[bar["session"]].get("splits")
                    allowed_latest = (source["provider"] == "nasdaq" and source["upstream"] == "Nasdaq US market data"
                                      and source["adjustment"] == "unadjusted_latest_session"
                                      and primary["provider"] == "yahoo" and primary["adjustment"] == "split"
                                      and bar["session"] == expected and split == 0)
                    if not allowed_latest:
                        item["issues"].append("nasdaq_adjustment_unverified_on_split_or_unknown_action_session")
                        comparisons.append({"session": bar["session"], "providers": [primary["provider"], source["provider"]],
                                            "scope": "adjustment_alignment", "status": "unknown"})
                        continue
                delta = abs(reference - bar["close"])
                passed = delta <= max(0.02, abs(reference) * close_tolerance)
                comparisons.append({"session": bar["session"], "providers": [primary["provider"], source["provider"]],
                                    "scope": "aligned_close", "absolute_difference": delta,
                                    "relative_difference": delta / abs(reference), "status": "pass" if passed else "fail"})
                if not passed:
                    conflict = True
        item["comparisons"] = comparisons
        if conflict:
            item["quality_status"] = "fail"
            item["issues"].append("cross_provider_price_conflict")
        elif authoritative or (len(upstreams) >= 2 and any(c["status"] == "pass" for c in comparisons)):
            item["quality_status"] = "pass"
        else:
            item["issues"].append("independent_price_confirmation_missing")
        if primary.get("missing_sessions"):
            item["quality_status"] = "unknown" if not conflict else "fail"
            item["issues"].append("incomplete_history_sessions")
            item["missing_sessions"] = primary["missing_sessions"]
            # Metrics with misleading session counts are not exposed as usable.
            item["indicators"] = {"basis": primary.get("indicator_basis"), "sample_count": len(primary["bars"]),
                                  "missing": ["consecutive exchange-session history required"]}
        if instrument["currency"] == "USD" and len(primary["bars"]) < minimum_history:
            item["quality_status"] = "unknown" if not conflict else "fail"
            item["issues"].append(f"insufficient_history: need {minimum_history} valid daily bars")
        if instrument["calendar"] == "SGE":
            item["issues"].append("retail_product_sell_buyback_quotes_required_for_purchase_price")
            if len(primary["bars"]) < minimum_history:
                item["issues"].append("benchmark_history_incomplete; missing indicators must not be inferred")
        if item["price"] <= 0 or not math.isfinite(item["price"]):
            item["quality_status"], item["price"] = "fail", None
    statuses = [item["quality_status"] for item in snapshot["instruments"].values()]
    snapshot["status"] = "ready" if all(value == "pass" for value in statuses) else "partial" if any(value != "fail" for value in statuses) else "blocked"
    snapshot["created_at"] = iso(clock())
    snapshot["collection"] = {"budget_seconds": max_collection_seconds,
                              "elapsed_seconds": round(max(0, time_fn() - budget_started), 3)}
    if time_fn() >= deadline:
        snapshot["issues"].append("collection_deadline_exceeded; uncollected evidence remains unknown")
    if parse_time(snapshot["valid_until"]) <= parse_time(snapshot["created_at"]):
        snapshot["status"] = "blocked"
        snapshot["issues"].append("snapshot_expired_during_collection")
    snapshot["snapshot_id"] = snapshot_digest(snapshot)
    return snapshot
