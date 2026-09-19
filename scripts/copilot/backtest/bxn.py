"""Cboe BXN as an index proxy for the income ETFs. Memory only, never on disk.

WHY A PROXY AT ALL
QQQI has 2.63 years of history, JEPQ 4.38, JEPI 6.33. None of them can ever
satisfy Q29 rule 1. BXN -- the Cboe Nasdaq-100 BuyWrite index -- is the
mechanical strategy those funds resemble, and it has daily closes back to
2009-09-18.

WHAT THIS PROVES AND WHAT IT DOES NOT
It proves how a mechanical monthly at-the-money buy-write behaved in 2020 and
2022. It does not prove how QQQI or JEPQ behaved: both are actively managed,
QQQI's issuer describes a strategy that "may include both sold and purchased NDX
index options", and the tracking error between fund and index is unmeasured.
Every output of this path is labelled index-proxy evidence. Writing
"QQQI passed the admission gate" anywhere is a defect.

WHY 2008 IS MISSING AND WHY WE DO NOT REACH FOR BXNT
BXN_History.csv begins 09/18/2009 and the start is fixed, not rolling -- a
2025-08-29 Wayback snapshot of the same URL begins on the same date. BXNT
reaches 1994, but no Cboe page links its CSV, Wayback holds zero snapshots of
it, its live-launch date appears on no page we fetched (so the back-test/live
boundary cannot be drawn), and QQQI's own issuer page names BXN four times and
BXNT zero times. ADR-0006 clause 5 grants one waiver, for 2008 only.

LICENCE -- THE REASON THERE IS NO CACHE IN THIS FILE
cboe.com/terms permits one copy for personal non-commercial use and forbids,
absent written consent, storing in an electronic retrieval system, distributing,
creating a derivative work, and using to verify other data. This module fetches
into memory and returns a frame. It opens no file. package_release.py refuses
any *_history.csv, and scripts/_test_backtest.py asserts this file contains no
write call.
"""
from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from datetime import datetime

from .frame import PriceFrame, build

CSV_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/BXN_History.csv"
SYMBOL = "^BXN"
EXPECTED_HEADER = ["DATE", "BXN"]
USER_AGENT = "Mozilla/5.0 TradingCopilot/1.0 (personal research)"

#: The single exception ADR-0006 grants to Q29 rule 1. Passed to
#: admission.assess(waivers=...), which keeps the failure visible in the report.
Q29_WAIVER = {
    "stress_2008": ("ADR-0006 clause 5: BXN's free daily file begins 2009-09-18 and the "
                    "start is fixed, not rolling; BXNT was rejected because its "
                    "back-test/live boundary cannot be established"),
}


def evidence_label(symbols: tuple[str, ...]) -> str:
    return (f"Index proxy evidence for {', '.join(symbols)}: Cboe BXN, a mechanical monthly "
            "at-the-money Nasdaq-100 buy-write, 2009-09-18 onward. Covers 2020 and 2022, not "
            "2008. Tracking error against the actual funds is unmeasured.")


def parse_csv(text: str) -> PriceFrame:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("BXN CSV is empty") from None
    if [h.strip().upper() for h in header] != EXPECTED_HEADER:
        raise ValueError(f"unexpected BXN CSV header {header!r}, expected {EXPECTED_HEADER!r}")
    dates, closes = [], []
    for row in reader:
        if len(row) != 2 or not row[0].strip():
            continue
        dates.append(datetime.strptime(row[0].strip(), "%m/%d/%Y").date())
        closes.append([float(row[1])])
    if not dates:
        raise ValueError("BXN CSV contained no data rows")
    return build(dates=dates, symbols=[SYMBOL], closes=closes)


def fetch(timeout: float = 30.0) -> PriceFrame:
    """Fetch into memory. Nothing is persisted; see the module docstring."""
    request = urllib.request.Request(CSV_URL, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "text/csv"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse_csv(response.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"BXN history unavailable: {exc}") from exc
