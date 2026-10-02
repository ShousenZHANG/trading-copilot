# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance==1.7.0", "exchange-calendars==4.13.2", "tzdata==2026.3"]
# ///
"""Shared conversation CLI. JSON input through stdin/files, never shell interpolation."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from runtime import force_utf8_stdio
from copilot import service

force_utf8_stdio()


def read_object(path: str) -> dict:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    value = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="isolated SQLite path; default data/state/copilot.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("snapshot")
    collect.add_argument("instruments", nargs="+")
    collect.add_argument("--horizon", choices=("daily", "swing", "long_term"), default="daily")
    sub.add_parser("capabilities")
    resolve = sub.add_parser("resolve", help="normalize one instrument against the shared registry")
    resolve.add_argument("--input", default="-", help="JSON with instrument_id, from a file or stdin")
    ctx = sub.add_parser("context")
    ctx.add_argument("instruments", nargs="*")
    ctx.add_argument("--sleeve", choices=("etf", "gold"), default="etf",
                     help="which book's coverage declaration to read: etf USD or gold CNY")
    snap = sub.add_parser("get-snapshot")
    snap.add_argument("snapshot_id")
    review = sub.add_parser("review")
    review.add_argument("snapshot_id")
    review.add_argument("--input", default="-", help="proposal JSON file or stdin")
    record = sub.add_parser("record")
    record.add_argument("--input", default="-", help="operation JSON file or stdin")
    record.add_argument("--idempotency-key", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    show = sub.add_parser("config", help="print validated config/user.toml as JSON")
    show.add_argument("--path", default=None)
    coverage_cmd = sub.add_parser("declare-coverage",
                                  help="record that recorded holdings are complete")
    coverage_cmd.add_argument("--sleeve", choices=("etf", "gold"), default="etf",
                              help="which book this declaration covers; the base currency "
                                   "follows from it (etf USD, gold CNY) and never crosses")
    quote_cmd = sub.add_parser("record-quote", help="attach a merchant gold quote you observed")
    quote_cmd.add_argument("snapshot_id")
    quote_cmd.add_argument("--merchant", required=True)
    quote_cmd.add_argument("--product", required=True)
    quote_cmd.add_argument("--ask-per-fine-gram", type=float, required=True)
    quote_cmd.add_argument("--observed-at", required=True,
                           help="ISO-8601 with a timezone offset, e.g. 2026-09-20T10:15:00+08:00")
    quote_cmd.add_argument("--account-id", default=None,
                           help="optional user-confirmed account identifier; use the same id as its fills")
    adopt_cmd = sub.add_parser(
        "adopt",
        help="print a stored adoption; read-only. Recording a NEW adoption is done by "
             "`backtest_cli.py --universe ... --family ... --adopt`, not here -- only that "
             "command holds the in-memory backtest Result the binding in ruleset.build_adoption "
             "requires (result.rule_name/parameters/universe must match what is being adopted), "
             "and a Result cannot survive a JSON round trip with that binding intact. A JSON "
             "adoption payload accepted here would let a caller describe a backtest that never "
             "ran, which is exactly what that binding exists to refuse.")
    adopt_cmd.add_argument("rule_id")
    evaluate_cmd = sub.add_parser("evaluate", help="run the adopted rule against a snapshot")
    evaluate_cmd.add_argument("snapshot_id")
    evaluate_cmd.add_argument("--sleeve", choices=("etf", "gold"), default="etf",
                              help="which sleeve's adopted_rule_id to run: etf (USD, whole "
                                   "shares) or gold (CNY, grams of Au99.99)")
    evaluate_cmd.add_argument("--brake-level", choices=("none", "reduce_50", "skip"),
                              default="none")
    evaluate_cmd.add_argument("--brake-reason", default="")
    evaluate_cmd.add_argument("--brake-evidence-id", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = service.collect(args.instruments, args.horizon, db_path=args.db)
        elif args.command == "capabilities":
            result = service.capabilities()
        elif args.command == "resolve":
            from copilot.instruments import normalize_instrument
            result = {"instrument_id": normalize_instrument(read_object(args.input)["instrument_id"])}
        elif args.command == "context":
            result = service.context(args.instruments or None, sleeve=args.sleeve, db_path=args.db)
        elif args.command == "get-snapshot":
            result = service.snapshot(args.snapshot_id, db_path=args.db)
        elif args.command == "review":
            result = service.review(args.snapshot_id, read_object(args.input), db_path=args.db)
        elif args.command == "record":
            result = service.record(read_object(args.input), args.idempotency_key, db_path=args.db)
        elif args.command == "config":
            from copilot.config import as_dict, load_config
            result = as_dict(load_config(args.path))
        elif args.command == "declare-coverage":
            # The currency follows from the sleeve rather than being a second
            # flag. There is exactly one currency per book -- the ETF sleeve is
            # USD and the gold sleeve is CNY -- and a declaration naming the
            # other one would be recorded happily by the journal and then fail
            # to fund anything, with nothing saying why.
            result = service.declare_coverage(
                sleeve=args.sleeve, base_currency={"etf": "USD", "gold": "CNY"}[args.sleeve],
                db_path=args.db)
        elif args.command == "record-quote":
            result = service.capture_retail_quote(
                snapshot_id=args.snapshot_id, merchant=args.merchant, product=args.product,
                ask_per_fine_gram=args.ask_per_fine_gram, observed_at=args.observed_at,
                account_id=args.account_id, db_path=args.db)
        elif args.command == "adopt":
            result = service.adoption(args.rule_id, db_path=args.db)
        elif args.command == "evaluate":
            result = service.evaluate(snapshot_id=args.snapshot_id, sleeve=args.sleeve,
                                      db_path=args.db,
                                      brake={"level": args.brake_level,
                                             "reason": args.brake_reason,
                                             "evidence_ids": args.brake_evidence_id})
        else:
            from copilot.journal import backup
            result = backup(args.destination, db_path=service.database_path(args.db))
        print(service.strict_json(result))
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error, ImportError) as exc:
        # Provider exceptions must be sanitized before crossing this interface.
        print(service.strict_json({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
