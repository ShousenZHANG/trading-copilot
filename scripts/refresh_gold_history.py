#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "akshare==1.17.99",
#   "pandas==2.2.3",
# ]
# ///
"""Refresh reference/sge-au9999-daily.csv from the Shanghai Gold Exchange.

WHY THIS IS A SEPARATE SCRIPT
scripts/copilot/ is stdlib-only and the offline CI matrix installs no
third-party packages, so akshare can never be imported from the code that runs
the backtest. This script is the boundary: it runs by hand, writes a plain CSV,
and the CSV is what ships. That is the same shape as admission.STRESS_SESSIONS
-- constants held in-repo for the offline job, re-verified by a runtime job.

WHY AKSHARE AND NOT THE REPO'S OWN SGEProvider
SGEProvider caps itself at 320 bars over at most 36 requests (providers.py:661,
:682) because it is a *snapshot* collector -- it exists to prove a freshness
watermark, not to page through a decade. Ten years is about 85 chunks. akshare's
spot_hist_sge returns the whole series in one call.

WHAT THIS IS NOT
It is not a second source. akshare scrapes sge.com.cn, the same upstream
SGEProvider reads, so CLAUDE.md's "same upstream through two wrappers is one
source" applies: gold has ONE price source and the snapshot records it as such.
Do not describe the agreement between them as corroboration.

Usage:
    uv run --no-project --quiet --script scripts/refresh_gold_history.py
    uv run --no-project --quiet --script scripts/refresh_gold_history.py --check
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SOURCE_URL = "https://www.sge.com.cn/sjzx/quotation_daily_new"
UPSTREAM = "Shanghai Gold Exchange"
SYMBOL = "Au99.99"
DATASET = Path(__file__).resolve().parent.parent / "reference" / "sge-au9999-daily.csv"


def pull() -> list[tuple[str, float]]:
    import akshare as ak

    frame = ak.spot_hist_sge(symbol=SYMBOL)
    rows: list[tuple[str, float]] = []
    for record in frame.to_dict("records"):
        session = record["date"]
        close = float(record["close"])
        if close <= 0:
            raise SystemExit(f"{session}: close must be positive, got {close}")
        rows.append((session.isoformat(), close))
    rows.sort(key=lambda pair: pair[0])
    if not rows:
        raise SystemExit("akshare returned no rows; refusing to write an empty dataset")
    return rows


def render(rows: list[tuple[str, float]], *, refreshed_at: str) -> str:
    lines = [f"# source_url: {SOURCE_URL}",
             f"# upstream: {UPSTREAM}",
             f"# refreshed_at: {refreshed_at}",
             "session,close"]
    lines.extend(f"{session},{close}" for session, close in rows)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="compare the live pull against the vendored file; write nothing")
    args = parser.parse_args(argv)

    rows = pull()
    print(f"pulled {len(rows)} sessions, {rows[0][0]} .. {rows[-1][0]}", file=sys.stderr)

    if args.check:
        if not DATASET.exists():
            print(f"{DATASET} does not exist", file=sys.stderr)
            return 1
        vendored = {}
        for line in DATASET.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or line.startswith("session,"):
                continue
            session, close = line.split(",")
            vendored[session] = float(close)
        drift = [(s, vendored.get(s), c) for s, c in rows
                 if s in vendored and abs(vendored[s] - c) > 1e-9]
        missing = [s for s, _ in rows if s not in vendored]
        for session, was, now in drift[:20]:
            print(f"DRIFT {session}: vendored {was}, live {now}", file=sys.stderr)
        if missing:
            print(f"{len(missing)} live session(s) absent from the vendored file, "
                  f"newest {missing[-1]}", file=sys.stderr)
        return 1 if (drift or missing) else 0

    refreshed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    # newline="" disables universal-newline translation. Path.write_text on
    # Windows would turn every "\n" from render() into "\r\n", so the file
    # committed from a Windows machine and the file a Linux refresh produces
    # would differ on all 2368 lines -- a whole-file diff carrying no price
    # change at all. The repo has no .gitattributes to paper over that.
    with DATASET.open("w", encoding="utf-8", newline="") as handle:
        handle.write(render(rows, refreshed_at=refreshed_at))
    print(f"wrote {DATASET} ({len(rows)} sessions, refreshed_at {refreshed_at})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
