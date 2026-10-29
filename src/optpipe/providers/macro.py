"""Macro event calendars for R08 (FOMC / CPI / PPI / NFP). Owner: Doodle (what counts) / Poodle (code).

Sources (read-only GET, cached; see config `sources`):
  * FOMC  - federalreserve.gov/monetarypolicy/fomccalendars.htm. Each scheduled meeting is an event
            on its LAST day at 14:00 ET (the customary statement time; the page itself does not state
            the time, so the field carries a note). Coverage runs through Dec 31 of the last year the
            page lists. Unscheduled meetings and notation votes cannot be known in advance and are skipped.
  * BLS   - bls.gov/schedule/news_release/{cpi,empsit,ppi}.htm "Schedule of Releases" tables (08:30 ET).
            Coverage runs from the fetch date through the last listed release date.
            NOTE: bls.gov answers scripted GETs with HTTP 403; live BLS coverage then stays `missing`
            (unless a cached copy fetched before the run exists).

A calendar is only usable by a run when it was fetched at or before the run's as_of (point in time).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional, Protocol

import yaml
from bs4 import BeautifulSoup

from ..records.fields import NY
from .base import DataUnavailable, Fetcher

MACRO_KINDS = ("FOMC", "CPI", "PPI", "NFP")
FOMC_TIME, BLS_TIME = time(14, 0), time(8, 30)
FOMC_TIME_NOTE = ("statement time 14:00 ET is customary; the Fed calendar page lists meeting dates only")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
BLS_NAMES = {"CPI": "Consumer Price Index", "PPI": "Producer Price Index", "NFP": "Employment Situation"}


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


# ---------------------------------------------------------------- providers
class MacroCalendarProvider(Protocol):
    def get_calendars(self, as_of: datetime) -> tuple[list[CalendarSnapshot], list[str]]: ...


class LiveMacroCalendars:
    """Read-only GETs (through a CachedFetcher). Any failure -> that kind's coverage stays missing."""

    def __init__(self, fetcher: Fetcher, fomc_url: str, bls_urls: dict[str, str]):
        self.fetcher, self.fomc_url, self.bls_urls = fetcher, fomc_url, bls_urls

    def get_calendars(self, as_of: datetime):
        snaps, notes = [], []
        try:
            r = self.fetcher.get("macro", "fomc", self.fomc_url)
            snaps.append(fomc_snapshot(r.text, r.url, r.fetched_at))
        except DataUnavailable as e:
            notes.append(f"FOMC calendar unavailable: {e}")
        for kind, url in self.bls_urls.items():
            try:
                r = self.fetcher.get("macro", f"bls-{kind.lower()}", url)
                snaps.append(bls_snapshot(kind, r.text, r.url, r.fetched_at))
            except DataUnavailable as e:
                notes.append(f"BLS {kind} schedule unavailable ({e}); bls.gov usually blocks scripted GETs (403)")
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
