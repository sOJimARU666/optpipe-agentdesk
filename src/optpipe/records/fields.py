"""Helpers to build schema-v1 Fields, TsPairs and ids. Owner: Poodle.

Every sourced datum is {value, source_id, as_of, status}. Market time is always America/New_York;
Berlin is stored alongside for Eugene (TsPair).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any, Optional
from zoneinfo import ZoneInfo

NY, BERLIN = ZoneInfo("America/New_York"), ZoneInfo("Europe/Berlin")


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime: {dt}")
    return dt.isoformat()


def ny(dt: datetime) -> datetime:
    return dt.astimezone(NY)


def tspair(dt: datetime) -> dict[str, str]:
    return {"ny": iso(dt.astimezone(NY)), "berlin": iso(dt.astimezone(BERLIN))}


def ny_at(d: date, hhmm: str | time) -> datetime:
    t = hhmm if isinstance(hhmm, time) else time.fromisoformat(hhmm)
    return datetime.combine(d, t, NY)


def F(value: Any, source_id: Optional[str], as_of: Optional[datetime], status: str = "ok", **kw) -> dict:
    """A sourced Field. Falls back to `missing` when the value is None."""
    if value is None and status in ("ok", "stale"):
        return MISS(source_id, kw.get("note"))
    d = {"value": value, "source_id": source_id, "as_of": iso(as_of) if as_of else None, "status": status}
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


def MISS(source_id: Optional[str] = None, note: Optional[str] = None) -> dict:
    d: dict[str, Any] = {"value": None, "source_id": source_id, "as_of": None, "status": "missing"}
    if note:
        d["note"] = note
    return d


def CONFLICT(alts: list[dict], note: Optional[str] = None) -> dict:
    d: dict[str, Any] = {"value": None, "source_id": None, "as_of": None, "status": "conflicting", "conflicts": alts}
    if note:
        d["note"] = note
    return d


def derived(value: Any, source_id: str, as_of: datetime, status: str, formula: str, **kw) -> dict:
    return F(value, source_id, as_of, status, method="derived", formula=formula, **kw)


def occ_symbol(root: str, expiry: date, right: str, strike: float) -> str:
    """OCC-style id, e.g. JPM261030C00340000 (strike x1000, 8 digits)."""
    return f"{root.upper()}{expiry:%y%m%d}{right.upper()[0]}{int(round(strike * 1000)):08d}"


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", s.lower()).strip("-")
    return s or "x"


def src_id(*parts: str) -> str:
    return "src-" + "-".join(slug(p) for p in parts)[:60]
