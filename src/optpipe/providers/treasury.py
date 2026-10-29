"""Risk-free rate for derived IV: U.S. Treasury "Daily Treasury Bill Rates" (home.treasury.gov CSV).

Uses the 13-week coupon-equivalent yield (configurable column) from the latest row dated BEFORE the
run's NY date (the day's row is published after the close), converted to a continuously compounded
rate: r = ln(1 + y * d/365) / (d/365), with d = 91 days for the 13-week bill.
Owner: Doodle (which rate) / Poodle (code).
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional, Protocol

import yaml

from ..records.fields import NY
from .base import DataUnavailable, Fetcher

TENOR_DAYS = {"4 WEEKS": 28, "6 WEEKS": 42, "8 WEEKS": 56, "13 WEEKS": 91, "17 WEEKS": 119,
              "26 WEEKS": 182, "52 WEEKS": 364}
PUBLISH_TIME = time(17, 0)       # approximation: rates are published after the close; the CSV has no time


@dataclass(frozen=True)
class RatePoint:
    rate: float                  # continuous, decimal
    yield_pct: float             # as published (percent, coupon equivalent)
    observed: date
    tenor: str
    source: dict                 # schema sources[] item

    @property
    def as_of(self) -> datetime:
        return datetime.combine(self.observed, PUBLISH_TIME, NY)

    @property
    def formula(self) -> str:
        d = TENOR_DAYS[self.tenor.replace(" COUPON EQUIVALENT", "").replace(" BANK DISCOUNT", "")]
        return (f"r = ln(1 + y*{d}/365)/({d}/365); y = {self.yield_pct}% Treasury {self.tenor.lower()} "
                f"on {self.observed.isoformat()} -> r = {self.rate:.6f}")


def to_continuous(yield_pct: float, days: int) -> float:
    t = days / 365
    return math.log(1 + yield_pct / 100 * t) / t


def parse_bill_rates(text: str) -> dict[date, dict[str, float]]:
    out: dict[date, dict[str, float]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            m, d, y = (int(x) for x in row["Date"].split("/"))
        except (KeyError, ValueError):
            continue
        vals = {}
        for k, v in row.items():
            if k != "Date" and v not in (None, "", "N/A"):
                try:
                    vals[k.strip()] = float(v)
                except ValueError:
                    pass
        out[date(y, m, d)] = vals
    return out


def latest_rate(text: str, before: date, tenor: str, source: dict) -> RatePoint:
    rows = parse_bill_rates(text)
    for d in sorted((d for d in rows if d < before), reverse=True):
        if tenor in rows[d]:
            y = rows[d][tenor]
            days = TENOR_DAYS[tenor.replace(" COUPON EQUIVALENT", "").replace(" BANK DISCOUNT", "")]
            return RatePoint(to_continuous(y, days), y, d, tenor, source)
    raise DataUnavailable(f"no Treasury '{tenor}' row before {before.isoformat()}")


def _source(sid: str, url: str, fetched_at: datetime, notes: Optional[str] = None) -> dict:
    d = {"id": sid, "url": url, "kind": "data_vendor", "fetched_at": fetched_at.isoformat(),
         "publisher": "U.S. Department of the Treasury", "title": "Daily Treasury Bill Rates"}
    if notes:
        d["notes"] = notes
    return d


class RateProvider(Protocol):
    def get_rate(self, as_of: datetime) -> RatePoint: ...


class LiveTreasuryRates:
    def __init__(self, fetcher: Fetcher, url_template: str, tenor: str):
        self.fetcher, self.url_t, self.tenor = fetcher, url_template, tenor

    def get_rate(self, as_of: datetime) -> RatePoint:
        d = as_of.astimezone(NY).date()
        url = self.url_t.format(year=d.year)
        r = self.fetcher.get("treasury", f"bill-rates-{d.year}", url)
        if r.fetched_at > as_of:
            raise DataUnavailable("Treasury rates fetched after as_of (point in time)")
        return latest_rate(r.text, d, self.tenor, _source("src-treasury-bill-rates", url, r.fetched_at))


class ReplayTreasuryRates:
    """Fixture mode: `rates:` block of the replay file (real CSV capture, hand-made fetch time)."""

    def __init__(self, path: Path, tenor: str):
        self.path, self.tenor = Path(path), tenor
        self.doc = yaml.safe_load(self.path.read_text())

    def get_rate(self, as_of: datetime) -> RatePoint:
        rb = self.doc.get("rates")
        if not rb:
            raise DataUnavailable("replay file has no rates block")
        fetched = datetime.fromisoformat(str(self.doc["fetched_at"]))
        if fetched > as_of:
            raise DataUnavailable("replayed Treasury fetch is after as_of (point in time)")
        text = (self.path.parent / rb["capture"]).read_text()
        src = _source(rb.get("source_id", "src-treasury-bill-rates"), rb["url"], fetched,
                      f"{self.doc['label']}. Content from a real capture: {rb['capture_note']}")
        return latest_rate(text, as_of.astimezone(NY).date(), self.tenor, src)
