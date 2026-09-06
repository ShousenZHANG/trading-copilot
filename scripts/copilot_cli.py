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
    ctx = sub.add_parser("context")
    ctx.add_argument("instruments", nargs="*")
    snap = sub.add_parser("get-snapshot")
    snap.add_argument("snapshot_id")
    review = sub.add_parser("review")
    review.add_argument("snapshot_id")
    review.add_argument("--input", default="-", help="proposal JSON file or stdin")
    record = sub.add_parser("record")
    record.add_argument("--input", default="-", help="operation JSON file or stdin")
    record.add_argument("--idempotency-key", required=True)
    prepare = sub.add_parser("prepare-run")
    prepare.add_argument("instruments", nargs="+")
    prepare.add_argument("--mode", choices=("tactical", "accumulation"), default="tactical")
    prepare.add_argument("--horizon", choices=("daily", "swing", "long_term"), default="daily")
    resume = sub.add_parser("resume-run")
    resume.add_argument("run_id")
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = service.collect(args.instruments, args.horizon, db_path=args.db)
        elif args.command == "capabilities":
            result = service.capabilities()
        elif args.command == "context":
            result = service.context(args.instruments or None, db_path=args.db)
        elif args.command == "get-snapshot":
            result = service.snapshot(args.snapshot_id, db_path=args.db)
        elif args.command == "review":
            result = service.review(args.snapshot_id, read_object(args.input), db_path=args.db)
        elif args.command == "record":
            result = service.record(read_object(args.input), args.idempotency_key, db_path=args.db)
        elif args.command == "prepare-run":
            result = service.prepare_run(args.instruments, mode=args.mode, horizon=args.horizon, db_path=args.db)
        elif args.command == "resume-run":
            result = service.resume_run(args.run_id, db_path=args.db)
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
