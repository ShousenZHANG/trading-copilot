"""Completed-session rules. No weekend-only fallbacks or guessed future holidays.

US schedule comes from exchange_calendars (DST and early closes included).
SGE dates use the exchange's own annual notices, not an equity-calendar proxy.
The gold adapter consumes SGE-labelled DAILY records, so Friday night is never
assigned to Friday by deriving a date from a raw trade timestamp.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
SGE_NOTICES = {
    2025: "https://www.sge.com.cn/jjsnotice/10006448",
    2026: "https://www.sge.com.cn/jjsnotice/10007108",
}
_SGE_CLOSURES = {
    2025: [("01-01", "01-01"), ("01-28", "02-04"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("05-31", "06-02"), ("10-01", "10-08")],
    2026: [("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"),
           ("10-01", "10-07")],
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an explicit timezone")
    return parsed.astimezone(UTC)


def iso(value: datetime) -> str:
    return parse_time(value).isoformat(timespec="seconds").replace("+00:00", "Z")


class CalendarUnavailable(ValueError):
    pass


class MarketCalendar:
    """Freshness watermarks are close + 30 minutes; refresh sooner for events.

    30 minutes is a conservative ingestion delay, not a claimed publication time.
    Every new recommendation fetches again even while valid_until remains future.
    """
    publication_delay = timedelta(minutes=30)

    def __init__(self):
        self._us = {}

    def _calendar(self, year: int):
        if year not in self._us:
            try:
                import exchange_calendars as xcals
                self._us[year] = xcals.get_calendar(
                    "XNYS", start=f"{year - 2}-01-01", end=f"{year + 1}-12-31")
            except (ImportError, ValueError, KeyError) as exc:
                raise CalendarUnavailable("US holiday calendar unavailable; install pinned runtime dependencies") from exc
        return self._us[year]

    def is_session(self, instrument: dict, session: str) -> bool:
        day = date.fromisoformat(session)
        if instrument["calendar"] == "SGE":
            if day.year not in _SGE_CLOSURES:
                raise CalendarUnavailable(f"SGE official holiday schedule for {day.year} is not verified")
            if day.weekday() >= 5:
                return False
            return not any(
                date.fromisoformat(f"{day.year}-{start}") <= day <= date.fromisoformat(f"{day.year}-{end}")
                for start, end in _SGE_CLOSURES[day.year]
            )
        return bool(self._calendar(day.year).is_session(session))

    def close(self, instrument: dict, session: str) -> datetime:
        if not self.is_session(instrument, session):
            raise CalendarUnavailable("bar date is not an exchange session")
        if instrument["calendar"] == "SGE":
            # SGE spot afternoon session ends at 15:30 Beijing time.
            return datetime.combine(date.fromisoformat(session), time(15, 30), ZoneInfo("Asia/Shanghai")).astimezone(UTC)
        return self._calendar(date.fromisoformat(session).year).session_close(session).to_pydatetime().astimezone(UTC)

    def window(self, instrument: dict, decision_at: datetime) -> tuple[str, datetime]:
        decision_at = parse_time(decision_at)
        day = decision_at.astimezone(ZoneInfo(instrument["timezone"])).date()
        expected = None
        for offset in range(40):
            candidate = (day - timedelta(days=offset)).isoformat()
            if self.is_session(instrument, candidate) and self.close(instrument, candidate) + self.publication_delay <= decision_at:
                expected = candidate
                break
        if expected is None:
            raise CalendarUnavailable("no completed session in verified calendar")
        for offset in range(1, 41):
            candidate = (date.fromisoformat(expected) + timedelta(days=offset)).isoformat()
            if self.is_session(instrument, candidate):
                return expected, min(self.close(instrument, candidate) + self.publication_delay,
                                     decision_at + timedelta(hours=12))
        raise CalendarUnavailable("next session is outside verified calendar")

    def metadata(self, instrument: dict, decision_at: datetime) -> dict:
        if instrument["calendar"] == "SGE":
            year = decision_at.astimezone(ZoneInfo("Asia/Shanghai")).year
            return {"calendar": "SGE", "source_url": SGE_NOTICES.get(year),
                    "verified_years": sorted(SGE_NOTICES), "session_label": "SGE trade date including preceding night session"}
        import importlib.metadata
        return {"calendar": "XNYS", "version": importlib.metadata.version("exchange_calendars"),
                "source_url": "https://github.com/gerrymanoim/exchange_calendars"}
