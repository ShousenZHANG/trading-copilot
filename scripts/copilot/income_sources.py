"""Public income-event adapters. No credentials, account reads or state writes.

Issuer HTML is preferred. Nasdaq's verified public ETF dividends endpoint is
an explicitly labelled exchange fallback, never an independent issuer source.
Page access and schema failures stay visible; a schedule is not a cash payment.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from .providers import ProviderError

ISSUER_URLS = {
    "QQQI": "https://neosfunds.com/qqqi/",
    "JEPQ": "https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-nasdaq-equity-premium-income-etf-etf-shares-46654q203",
}
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/html"}
EXCHANGE_HEADERS = {**HEADERS, "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}


def exchange_url(symbol):
    if symbol not in ISSUER_URLS:
        raise ValueError("income instrument unsupported")
    return f"https://api.nasdaq.com/api/quote/{symbol}/dividends?assetclass=etf"


class SourceError(ValueError):
    """Fixed diagnostic; source bodies and upstream exception text stay local."""

    def __init__(self, code, *, diagnostics=None):
        super().__init__(code)
        self.diagnostics = list(diagnostics or [])


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.rows, self.row, self.cell = [], None, None, None
        self.headings, self.heading = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "h1":
            self.heading = []
        if tag == "table":
            self.rows = []
        elif tag == "tr" and self.rows is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        if self.heading is not None:
            self.heading.append(data)

    def handle_endtag(self, tag):
        if tag == "h1" and self.heading is not None:
            self.headings.append(" ".join("".join(self.heading).split()))
            self.heading = None
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.tables.append(self.rows)
            self.rows = None


def parse_issuer_html(symbol, body):
    """Parse only a named five-column issuer distribution table, not yield text."""
    if not isinstance(body, str) or len(body) > 3_000_000:
        raise SourceError("issuer_payload_invalid")
    parser = _Tables()
    parser.feed(body)
    if symbol == "QQQI" and "QQQI" not in parser.headings:
        raise SourceError("issuer_instrument_identity_missing")
    if symbol == "JEPQ" and "46654Q203" not in body and "JEPQ" not in parser.headings:
        raise SourceError("issuer_instrument_identity_missing")
    aliases = {"declarationdate": "declaration_date", "exdivdate": "ex_date",
               "exdividenddate": "ex_date", "exdate": "ex_date", "recorddate": "record_date",
               "payabledate": "pay_date", "paymentdate": "pay_date", "paydate": "pay_date",
               "amount": "amount_per_share", "amountpershare": "amount_per_share"}
    rows = []
    for table in parser.tables:
        columns = None
        for cells in table:
            names = [aliases.get(re.sub(r"[^a-z]", "", cell.lower())) for cell in cells]
            if set(names) >= {"declaration_date", "ex_date", "record_date", "pay_date", "amount_per_share"}:
                columns = names
                continue
            if columns and len(cells) == len(columns):
                row = {name: cell for name, cell in zip(columns, cells) if name}
                row.update(currency="USD", amount_kind="actual" if row["amount_per_share"] else "scheduled")
                rows.append(row)
    if not rows:
        raise SourceError("issuer_distribution_table_unavailable")
    return rows


def parse_exchange_json(symbol, body):
    """The schema was verified on Nasdaq's live JEPQ endpoint on 2026-10-05."""
    try:
        payload = json.loads(body) if isinstance(body, str) else body
        if payload.get("status", {}).get("rCode") != 200:
            raise SourceError("exchange_response_not_success")
        data = payload["data"]["dividends"]
        raw = data["rows"]
        if not isinstance(raw, list) or not 1 <= len(raw) <= 1000:
            raise SourceError("exchange_event_rows_invalid")
        total = data.get("totalRecords")
        if total is not None and int(total) > len(raw):
            raise SourceError("exchange_history_truncated")
        rows = []
        for row in raw:
            if row.get("type") != "Cash" or row.get("currency") != "USD":
                raise SourceError("exchange_distribution_type_or_currency_unsupported")
            rows.append({"amount_per_share": row["amount"], "declaration_date": row["declarationDate"],
                             "ex_date": row["exOrEffDate"], "record_date": row["recordDate"],
                             "pay_date": row["paymentDate"], "currency": "USD", "amount_kind": "actual"})
        return rows
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise SourceError("exchange_payload_invalid") from exc


def fetch_events(symbol, client, *, allow_exchange_fallback=True):
    """Return one actual response, its parsed events, and failed-source diagnostics."""
    if symbol not in ISSUER_URLS:
        raise SourceError("income_instrument_unsupported")
    diagnostics = []
    sources = [(ISSUER_URLS[symbol], "issuer", parse_issuer_html)]
    if allow_exchange_fallback:
        sources.append((exchange_url(symbol), "exchange", parse_exchange_json))
    for url, role, parser in sources:
        try:
            response = client.get(url, "income_" + role, headers=EXCHANGE_HEADERS if role == "exchange" else HEADERS)
            if response.get("source_url") != url or response.get("cache_status") not in {None, "live"}:
                raise SourceError("income_source_binding_invalid")
            rows = parser(symbol, response["body"])
            return {"rows": rows, "response": response, "source_url": url, "source_role": role,
                        "source_family": "NEOS" if symbol == "QQQI" and role == "issuer" else "JPM" if role == "issuer" else "Nasdaq",
                        "diagnostics": diagnostics}
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, ProviderError) as exc:
            code = str(exc) if isinstance(exc, SourceError) else "income_source_unavailable:" + type(exc).__name__
            diagnostics.append({"source_url": url, "source_role": role, "code": code})
    raise SourceError("income_all_sources_blocked", diagnostics=diagnostics)
