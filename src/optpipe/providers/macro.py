"""Macro event calendars for R08 (FOMC / CPI / PPI / NFP). Owner: Doodle (what counts) / Poodle (code).

Sources (read-only GET, cached; see config `sources`):
  * FOMC  - federalreserve.gov/monetarypolicy/fomccalendars.htm. Each scheduled meeting is an event
            on its LAST day at 14:00 ET (the customary statement time; the page itself does not state
            the time, so the field carries a note). Coverage runs through Dec 31 of the last year the
            page lists. Unscheduled meetings and notation votes cannot be known in advance and are skipped.
  * BLS   - live: ONE GET of bls.gov/schedule/news_release/bls.ics (VEVENTs; SUMMARY 'Consumer Price Index',
            'Producer Price Index', 'Employment Situation'; DTSTART;TZID=US-Eastern, 08:30 ET).
            bls.gov answers 403 unless the User-Agent carries a contact email (Doodle, spec
            bls_release_sources.md), so the request uses `optpipe/1.1 (calendar fetch; contact: <email>)`
            with the email from config `sources.bls_contact_email`. That value is EMPTY by default; while
            it is empty the BLS fetch is SKIPPED (never sent without a contact).
            Fallback per missing kind: FRED's public release calendar
            fred.stlouisfed.org/releases/calendar?rid=<id>&y=<year> (rid 10 = CPI, 46 = PPI, 50 = NFP).
            FRED prints times in US Central Time ("All times are US Central Time"): 7:30 am CT = 08:30 ET.
            Fixture/replay mode still reads the captured BLS "Schedule of Releases" pages (parse_bls).
            Coverage runs from the fetch date through the last listed release date of that kind.

A calendar is only usable by a run when it was fetched at or before the run's as_of (point in time).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Optional, Protocol

import yaml
from bs4 import BeautifulSoup

from zoneinfo import ZoneInfo

from ..records.fields import NY
from .base import DataUnavailable, Fetcher

MACRO_KINDS = ("FOMC", "CPI", "PPI", "NFP")
FOMC_TIME, BLS_TIME = time(14, 0), time(8, 30)
FOMC_TIME_NOTE = ("statement time 14:00 ET is customary; the Fed calendar page lists meeting dates only")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
BLS_NAMES = {"CPI": "Consumer Price Index", "PPI": "Producer Price Index", "NFP": "Employment Situation"}
BLS_KIND_BY_SUMMARY = {v.lower(): k for k, v in BLS_NAMES.items()}
FRED_RELEASE_IDS = {"CPI": 10, "PPI": 46, "NFP": 50}
FRED_TZ = ZoneInfo("America/Chicago")         # FRED: "All times are US Central Time"
BLS_UA_TEMPLATE = "optpipe/1.1 (calendar fetch; contact: {email})"
_EMAIL = re.compile(r"^[^@\s()<>;]+@[^@\s()<>;]+\.[^@\s()<>;]+$")
_ICS_TZ = {"US-Eastern": NY, "America/New_York": NY, "US/Eastern": NY}


def bls_user_agent(contact_email: Optional[str]) -> Optional[str]:
    """UA for bls.gov, or None when no usable contact email is configured (then BLS is not fetched)."""
    e = (contact_email or "").strip()
    return BLS_UA_TEMPLATE.format(email=e) if _EMAIL.match(e) else None


def _month(s: str) -> int:
    return _MONTHS[s.strip().lower()[:3]]


@dataclass(frozen=True)
class MacroEvent:
    kind: str
    at: datetime                 # America/New_York
    description: str
    note: Optional[str] = None


@dataclass
class CalendarSnapshot:
    kind: str
    source: dict                 # schema `sources[]` item (id, url, kind, fetched_at, ...)
    events: list[MacroEvent]
    checked_through: Optional[date]
    formula: str
    notes: list[str] = field(default_factory=list)

    @property
    def fetched_at(self) -> datetime:
        return datetime.fromisoformat(self.source["fetched_at"])


# ---------------------------------------------------------------- parsers
def parse_fomc(html: str) -> tuple[list[tuple[date, bool]], Optional[int]]:
    """-> ([(decision_day, has_SEP)], last listed year). Skips notation votes / unscheduled entries."""
    soup = BeautifulSoup(html, "lxml")
    meetings: list[tuple[date, bool]] = []
    years: list[int] = []
    for panel in soup.select("div.panel.panel-default"):
        head = panel.select_one(".panel-heading")
        m = re.search(r"(\d{4})\s+FOMC Meetings", head.get_text(" ", strip=True)) if head else None
        if not m:
            continue
        year = int(m.group(1))
        years.append(year)
        for row in panel.select(".fomc-meeting"):
            mon = row.select_one(".fomc-meeting__month")
            day = row.select_one(".fomc-meeting__date")
            if not mon or not day:
                continue
            mtxt, dtxt = mon.get_text(" ", strip=True), day.get_text(" ", strip=True)
            if "(" in dtxt or "unscheduled" in dtxt.lower():      # "22 (notation vote)", "(unscheduled)"
                continue
            dm = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})(\*?)", dtxt.replace(" ", ""))
            if not dm:
                continue
            months = [x for x in re.split(r"[/-]", mtxt) if x.strip()]
            d1, d2 = int(dm.group(1)), int(dm.group(2))
            last_month = _month(months[-1]) if (len(months) > 1 and d2 < d1) else _month(months[0])
            meetings.append((date(year, last_month, d2), bool(dm.group(3))))
    return sorted(set(meetings)), (max(years) if years else None)


_BLS_DATE = re.compile(r"([A-Za-z]{3,5})\.?\s+(\d{1,2}),\s*(\d{4})")
_BLS_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([AP])\.?M", re.I)


def parse_bls(text: str) -> list[tuple[str, datetime]]:
    """BLS 'Schedule of Releases' table, as HTML (live) or the markdown rendering (captures).
    -> [(reference month, release datetime NY)]"""
    rows: list[list[str]] = []
    if "<table" in text.lower():
        soup = BeautifulSoup(text, "lxml")
        for tr in soup.select("table tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) >= 3:
                rows.append(cells[:3])
    else:
        for line in text.splitlines():
            if line.lstrip().startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 3:
                    rows.append(cells[:3])
    out = []
    for ref, d, t in rows:
        dm, tm = _BLS_DATE.search(d), _BLS_TIME.search(t)
        if not dm:
            continue
        try:
            day = date(int(dm.group(3)), _month(dm.group(1)), int(dm.group(2)))
        except (KeyError, ValueError):
            continue
        hh, mm = (int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == "P" else 0), int(tm.group(2))) if tm \
            else (BLS_TIME.hour, BLS_TIME.minute)
        out.append((ref, datetime.combine(day, time(hh, mm), NY)))
    return sorted(out, key=lambda x: x[1])


# ---------------------------------------------------------------- snapshots
def _ics_lines(text: str) -> list[str]:
    """RFC 5545 unfolding: a line starting with a space/tab continues the previous one."""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _ics_unescape(v: str) -> str:
    return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _ics_dt(params: str, value: str, default_tz) -> Optional[datetime]:
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?", value.strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if m.group(4) is None:                                   # all-day: BLS releases are at 08:30 ET
        return datetime.combine(date(y, mo, d), BLS_TIME, NY)
    hh, mm = int(m.group(4)), int(m.group(5))
    if m.group(7):
        return datetime(y, mo, d, hh, mm, tzinfo=timezone.utc).astimezone(NY)
    tzm = re.search(r"TZID=([^;:]+)", params)
    tz = _ICS_TZ.get(tzm.group(1).strip('"'), None) if tzm else default_tz
    if tz is None:
        try:
            tz = ZoneInfo(tzm.group(1).strip('"'))
        except Exception:
            tz = default_tz
    return datetime(y, mo, d, hh, mm, tzinfo=tz).astimezone(NY)


def parse_bls_ics(text: str) -> list[tuple[str, datetime, str]]:
    """BLS release calendar (bls.ics) -> [(kind, at NY, summary)] for CPI/PPI/NFP only, by EXACT SUMMARY
    ('Consumer Price Index', 'Producer Price Index', 'Employment Situation'); everything else
    (Real Earnings, JOLTS, regional CPI, ...) is ignored. Floating times use X-WR-TIMEZONE (else ET)."""
    lines = _ics_lines(text)
    default_tz = NY
    for ln in lines:
        if ln.upper().startswith("X-WR-TIMEZONE:"):
            default_tz = _ICS_TZ.get(ln.split(":", 1)[1].strip(), NY)
    out, ev = [], None
    for ln in lines:
        u = ln.upper()
        if u == "BEGIN:VEVENT":
            ev = {}
        elif u == "END:VEVENT":
            if ev and ev.get("summary") and ev.get("at"):
                kind = BLS_KIND_BY_SUMMARY.get(ev["summary"].strip().lower())
                if kind:
                    out.append((kind, ev["at"], ev["summary"].strip()))
            ev = None
        elif ev is not None and ":" in ln:
            name, value = ln.split(":", 1)
            key, _, params = name.partition(";")
            if key.upper() == "SUMMARY":
                ev["summary"] = _ics_unescape(value)
            elif key.upper() == "DTSTART":
                ev["at"] = _ics_dt(params, value, default_tz)
    return sorted(set(out), key=lambda x: (x[1], x[0]))


_FRED_DAY = re.compile(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})")
_FRED_TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap])\.?m\.?$", re.I)


def parse_fred_calendar(html: str, release_name: str) -> list[datetime]:
    """FRED release calendar page -> release datetimes in NY for rows named `release_name`.
    The page is a table of day-header rows ('Tuesday November 10, 2026') followed by
    ('7:30 am', '<release>') rows, in US Central Time."""
    soup = BeautifulSoup(html, "lxml")
    if "central time" not in soup.get_text(" ", strip=True).lower():
        raise DataUnavailable("FRED calendar page no longer states 'US Central Time'; refusing to guess the zone")
    out, day = [], None
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue
        m = _FRED_DAY.search(" ".join(cells))
        if m and len(cells) == 1:
            day = date(int(m.group(3)), _month(m.group(1)), int(m.group(2)))
            continue
        if day and len(cells) >= 2 and cells[1].strip().lower() == release_name.lower():
            tm = _FRED_TIME.match(cells[0].strip())
            if not tm:
                continue
            hh = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "p" else 0)
            out.append(datetime(day.year, day.month, day.day, hh, int(tm.group(2)), tzinfo=FRED_TZ).astimezone(NY))
    return sorted(set(out))


def _source(sid: str, url: str, fetched_at: datetime, publisher: str, title: str, notes: Optional[str] = None,
            http_status: Optional[int] = None) -> dict:
    d = {"id": sid, "url": url, "kind": "economic_calendar", "fetched_at": fetched_at.isoformat(),
         "publisher": publisher, "title": title}
    if notes:
        d["notes"] = notes
    if http_status is not None:
        d["http_status"] = http_status
    return d


def fomc_snapshot(html: str, url: str, fetched_at: datetime, sid: str = "src-fed-fomc",
                  source_notes: Optional[str] = None, extra: Optional[list[MacroEvent]] = None) -> CalendarSnapshot:
    meetings, last_year = parse_fomc(html)
    if not meetings or last_year is None:
        raise DataUnavailable("FOMC calendar page: no meetings parsed")
    since = fetched_at.astimezone(NY).date()
    ev = [MacroEvent("FOMC", datetime.combine(d, FOMC_TIME, NY),
                     f"FOMC rate decision{' + SEP' if sep else ''} (meeting ends {d.isoformat()})", FOMC_TIME_NOTE)
          for d, sep in meetings if d >= since]
    return CalendarSnapshot(
        "FOMC", _source(sid, url, fetched_at, "Board of Governors of the Federal Reserve System",
                        "Meeting calendars and information", source_notes),
        sorted(ev + (extra or []), key=lambda e: e.at), date(last_year, 12, 31),
        f"Dec 31 of the last year listed on the calendar page ({last_year})",
        ["FOMC coverage excludes unscheduled meetings/notation votes (not knowable in advance)"])


def bls_snapshot(kind: str, text: str, url: str, fetched_at: datetime, sid: Optional[str] = None,
                 source_notes: Optional[str] = None, extra: Optional[list[MacroEvent]] = None) -> CalendarSnapshot:
    rel = parse_bls(text)
    if not rel:
        raise DataUnavailable(f"BLS {kind} schedule: no release rows parsed")
    since = fetched_at.astimezone(NY).date()
    ev = [MacroEvent(kind, at, f"{BLS_NAMES[kind]} ({ref} data)") for ref, at in rel if at.date() >= since]
    ev += extra or []
    return CalendarSnapshot(
        kind, _source(sid or f"src-bls-{kind.lower()}-schedule", url, fetched_at, "U.S. Bureau of Labor Statistics",
                      f"Schedule of Releases for the {BLS_NAMES[kind]}", source_notes),
        sorted(ev, key=lambda e: e.at), max(e.at for e in ev).date(),
        "last release date listed in the BLS schedule; releases before the fetch date are in the past")


def bls_ics_snapshots(text: str, url: str, fetched_at: datetime, sid: str = "src-bls-ics",
                      source_notes: Optional[str] = None) -> list[CalendarSnapshot]:
    """One snapshot per kind found in the ICS (kinds absent from the file are simply not returned)."""
    rows = parse_bls_ics(text)
    since = fetched_at.astimezone(NY).date()
    src = _source(sid, url, fetched_at, "U.S. Bureau of Labor Statistics",
                  "BLS.gov Economic News Release Schedule (iCalendar)", source_notes)
    snaps = []
    for kind in ("CPI", "PPI", "NFP"):
        mine = [at for k, at, _ in rows if k == kind]
        if not mine:
            continue
        ev = [MacroEvent(kind, at, BLS_NAMES[kind]) for at in mine if at.date() >= since]
        snaps.append(CalendarSnapshot(kind, dict(src), ev, max(mine).date(),
                                      f"last '{BLS_NAMES[kind]}' VEVENT in bls.ics; releases before the fetch date are in the past"))
    return snaps


def fred_snapshot(kind: str, pages: list[tuple[str, str]], fetched_at: datetime, sid: Optional[str] = None,
                  source_notes: Optional[str] = None) -> CalendarSnapshot:
    """pages = [(url, html)] for consecutive years. Coverage = last listed date across them."""
    ats: list[datetime] = []
    for _, html in pages:
        ats += parse_fred_calendar(html, BLS_NAMES[kind])
    if not ats:
        raise DataUnavailable(f"FRED {kind} calendar: no '{BLS_NAMES[kind]}' rows parsed")
    since = fetched_at.astimezone(NY).date()
    ev = [MacroEvent(kind, at, BLS_NAMES[kind], "FRED lists US Central Time; converted to ET")
          for at in sorted(set(ats)) if at.date() >= since]
    rid = FRED_RELEASE_IDS[kind]
    return CalendarSnapshot(
        kind, _source(sid or f"src-fred-rid{rid}", pages[0][0], fetched_at, "Federal Reserve Bank of St. Louis (FRED)",
                      f"FRED release calendar: {BLS_NAMES[kind]} (rid {rid})", source_notes),
        ev, max(ats).date(),
        f"last '{BLS_NAMES[kind]}' date on the FRED release calendar page(s) fetched (fallback for BLS)")


# ---------------------------------------------------------------- providers
class MacroCalendarProvider(Protocol):
    def get_calendars(self, as_of: datetime) -> tuple[list[CalendarSnapshot], list[str]]: ...


class LiveMacroCalendars:
    """Read-only GETs (through CachedFetchers). Any failure -> that kind's coverage stays missing.

    `bls_fetcher` is a fetcher whose User-Agent carries the configured contact email
    (see bls_user_agent); pass None when no email is configured and BLS is skipped entirely.
    Kinds the BLS ICS did not provide fall back to FRED (plain fetcher, no email needed)."""

    def __init__(self, fetcher: Fetcher, fomc_url: str, bls_fetcher: Optional[Fetcher] = None,
                 bls_ics_url: str = "https://www.bls.gov/schedule/news_release/bls.ics",
                 fred_url: str = "https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={year}",
                 fred_release_ids: Optional[dict[str, int]] = None, fred_next_year_from_month: int = 10):
        self.fetcher, self.fomc_url, self.bls_fetcher = fetcher, fomc_url, bls_fetcher
        self.bls_ics_url, self.fred_url = bls_ics_url, fred_url
        self.fred_ids = fred_release_ids or dict(FRED_RELEASE_IDS)
        self.next_year_from = fred_next_year_from_month

    def get_calendars(self, as_of: datetime):
        snaps, notes = [], []
        try:
            r = self.fetcher.get("macro", "fomc", self.fomc_url)
            snaps.append(fomc_snapshot(r.text, r.url, r.fetched_at))
        except DataUnavailable as e:
            notes.append(f"FOMC calendar unavailable: {e}")
        have: set[str] = set()
        if self.bls_fetcher is None:
            notes.append("BLS live fetch skipped: sources.bls_contact_email is empty (bls.gov needs a contact "
                         "email in the User-Agent); using the FRED release calendar instead")
        else:
            try:
                r = self.bls_fetcher.get("macro", "bls-ics", self.bls_ics_url)
                got = bls_ics_snapshots(r.text, r.url, r.fetched_at)
                snaps += got
                have = {s.kind for s in got}
                if not got:
                    notes.append("BLS bls.ics fetched but no CPI/PPI/Employment Situation VEVENTs parsed")
            except DataUnavailable as e:
                notes.append(f"BLS bls.ics unavailable ({e}); falling back to FRED")
        local = as_of.astimezone(NY)
        years = [local.year] + ([local.year + 1] if local.month >= self.next_year_from else [])
        for kind in ("CPI", "PPI", "NFP"):
            if kind in have or kind not in self.fred_ids:
                continue
            pages, fetched = [], None
            for y in years:
                url = self.fred_url.format(rid=self.fred_ids[kind], year=y)
                try:
                    r = self.fetcher.get("macro", f"fred-{kind.lower()}-{y}", url)
                except DataUnavailable as e:
                    notes.append(f"FRED {kind} {y} calendar unavailable: {e}")
                    continue
                pages.append((r.url, r.text))
                fetched = r.fetched_at if fetched is None else max(fetched, r.fetched_at)
            if not pages:
                continue
            try:
                snaps.append(fred_snapshot(kind, pages, fetched))
            except DataUnavailable as e:
                notes.append(str(e))
        return snaps, notes


class ReplayMacroCalendars:
    """Fixture mode: a HAND-MADE replay file describing what a fetch at `fetched_at` would have returned,
    built from real captures (see the file's `label`). No network."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.doc = yaml.safe_load(self.path.read_text())

    def _extra(self, kind: str, items: list[dict]) -> list[MacroEvent]:
        return [MacroEvent(kind, datetime.combine(date.fromisoformat(str(x["date"])),
                                                  time.fromisoformat(str(x.get("time", "08:30"))), NY),
                           x["description"], x.get("basis")) for x in items or []]

    def get_calendars(self, as_of: datetime):
        d = self.doc
        fetched = datetime.fromisoformat(str(d["fetched_at"]))
        label = d["label"]
        snaps, notes = [], [f"macro calendars: {label}"]
        for c in d.get("calendars", []):
            kind = c["kind"]
            text = (self.path.parent / c["capture"]).read_text()
            sn = f"{label}. Content from a real capture: {c['capture_note']}"
            extra = self._extra(kind, c.get("extra_events", []))
            if kind == "FOMC":
                s = fomc_snapshot(text, c["url"], fetched, c.get("source_id", "src-fed-fomc"), sn, extra)
            else:
                s = bls_snapshot(kind, text, c["url"], fetched, c.get("source_id"), sn, extra)
            if c.get("coverage_note"):
                s.notes.append(c["coverage_note"])
            snaps.append(s)
        return snaps, notes


def visible(snaps: list[CalendarSnapshot], as_of: datetime) -> tuple[list[CalendarSnapshot], list[str]]:
    keep, notes = [], []
    for s in snaps:
        if s.fetched_at <= as_of:
            keep.append(s)
        else:
            notes.append(f"{s.kind} calendar fetched {s.fetched_at.isoformat()} is after as_of; ignored (point in time)")
    return keep, notes
