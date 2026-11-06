"""App-side, versioned NYSE calendar with an explicit coverage bound (FEAT-OODLE-01 PR B, MD-19-CALENDAR (proposed)).

PAPER ONLY, pure data + date arithmetic, no network. All logic is in America/New_York (MD-19-TZ (proposed)): NY wall
times are built through zoneinfo, never from a fixed offset.

Why app-side: spec 1.1.8's risk_rules.yaml market_hours lists 2026 only. Moving that file (one versioned artefact
with covers_from / covers_to) needs a spec release (proposal Q-5, still OPEN; this module does not edit spec/).
optpipe.risk.engine.Calendar unions this table onto the spec list, so a 2027 holiday is not a session and
2027-11-26 closes at 13:00. 2026 stays pinned to risk_rules.yaml. Outside [COVERS_FROM, COVERS_TO] every answer is
None (UNKNOWN), and the consumers turn that into NO IDEA (fail closed).

Sources (read 2026-11-03): 2026 = spec 1.1.8 risk_rules.yaml config.market_hours; 2026 and 2027 checked against
https://www.nyse.com/markets/hours-calendars ("Holidays & Trading Hours", 2026 / 2027 / 2028 table and its early-close
footnotes: 13:00 ET on 2026-11-27, 2026-12-24 and 2027-11-26).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
VERSION = "optpipe-nyse-2026-2027.1"
COVERS_FROM = date(2026, 1, 1)
COVERS_TO = date(2027, 12, 31)
SOURCE = "https://www.nyse.com/markets/hours-calendars"
SOURCE_READ = "2026-11-03"
OPEN, CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)
HOLIDAYS = frozenset(date.fromisoformat(d) for d in (
    # 2026 (= spec risk_rules.yaml market_hours.holidays)
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027 (nyse.com, read 2026-11-03)
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
))
EARLY_CLOSES = frozenset(date.fromisoformat(d) for d in ("2026-11-27", "2026-12-24", "2027-11-26"))


def covers(d: date) -> bool:
    return COVERS_FROM <= d <= COVERS_TO


def is_session(d: date) -> Optional[bool]:
    """True / False for a covered date, None (UNKNOWN) outside the coverage"""
    if not covers(d):
        return None
    return d.weekday() < 5 and d not in HOLIDAYS


def ny_at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, NY)


def close_at(d: date) -> Optional[datetime]:
    """the official close of session d (16:00, 13:00 on an early close), None when d is no session or not covered"""
    if not is_session(d):
        return None
    return ny_at(d, EARLY_CLOSE if d in EARLY_CLOSES else CLOSE)


def prev_session(d: date) -> Optional[date]:
    """the last session strictly before d, None when that walk leaves the coverage"""
    x = d - timedelta(days=1)
    while True:
        s = is_session(x)
        if s is None:
            return None
        if s:
            return x
        x -= timedelta(days=1)


def next_session(d: date) -> Optional[date]:
    """the first session strictly after d, None when that walk leaves the coverage"""
    x = d + timedelta(days=1)
    while True:
        s = is_session(x)
        if s is None:
            return None
        if s:
            return x
        x += timedelta(days=1)


def last_completed_session(t: datetime) -> Optional[date]:
    """the latest session whose official close is at or before t (t must be aware), None outside the coverage"""
    d = t.astimezone(NY).date()
    s = is_session(d)
    if s is None:
        return None
    c = close_at(d) if s else None
    if c is not None and c <= t:
        return d
    return prev_session(d)


def sessions_after(a: date, b: date) -> Optional[int]:
    """number of sessions in (a, b], None if any day in between is outside the coverage (b >= a)"""
    if b < a:
        return None
    n, x = 0, a
    while x < b:
        x += timedelta(days=1)
        s = is_session(x)
        if s is None:
            return None
        n += int(s)
    return n
