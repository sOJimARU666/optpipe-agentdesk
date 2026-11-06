"""Pack-backed market-data providers (FEAT-OODLE-01 PR B).

Discovery, research and the watcher read Oodle's market-data packs through optpipe.marketdata (fail closed) and
optpipe.mdfeeds (per-feed freshness, calendar, R07 / R08). PAPER ONLY: pure file I/O, no network. The HTTP fetchers
stay in the tree for PR C; this module never imports them.

A PackMarket is bound to one loaded PackResult and the injected decision clock `now`. Quotes, chains and events come
from the pack's ticker files and catalysts.json. Screens come from the pack index's tickers (grouped by watchlist), so
discovery no longer needs Finviz when running from packs. Ideas may use only marketdata.usable_contracts() (Moodle
to-do c): get_chain returns those, and only those, for a ticker that passes mdfeeds.decision_reasons().
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from .. import mdfeeds, marketdata as md
from ..models.common import (Bias, Event, EventKind, EventTiming, OptionChain, OptionQuote, QuoteBasis, Right,
                             ScreenResult, ScreenRow, UnderlyingQuote)
from ..providers.base import DataUnavailable
from ..providers.macro import CalendarSnapshot, MacroEvent


_USE_PACK_HOLD = object()


def _val(f) -> Any:
    return md.usable_value(f) if isinstance(f, dict) else None


def _as_of(f) -> Optional[datetime]:
    return md._aware((f or {}).get("as_of") or "") if isinstance(f, dict) else None


def _right(s: str) -> Right:
    return Right.CALL if str(s).lower().startswith("c") else Right.PUT


def _iv_number(f) -> Optional[float]:
    """The pack's quoted IV when the field carries a finite number (OK or STALE). None when it is absent."""
    if not isinstance(f, dict):
        return None
    v = f.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


_KIND_WORD = re.compile(r"\b(CPI|PPI|NFP|FOMC)\b", re.I)


def _macro_kind(c: dict) -> Optional[str]:
    """FOMC is catalyst_type fomc. CPI / PPI / NFP have no schema enum value; a macro_release note names the kind."""
    raw = str(c.get("catalyst_type") or "").upper()
    if raw in mdfeeds.MACRO_KINDS:
        return raw
    if raw == "MACRO_RELEASE":
        m = _KIND_WORD.search(str(c.get("note") or ""))
        if m and m.group(1).upper() in ("CPI", "PPI", "NFP"):
            return m.group(1).upper()
    return None


def _source_read_times(c: dict) -> list[datetime]:
    srcs = c.get("sources") if isinstance(c.get("sources"), list) else []
    out = []
    for s in srcs:
        if isinstance(s, dict):
            t = md._aware(str(s.get("as_of") or ""))
            if t is not None:
                out.append(t)
    return out


class PackMacroCalendars:
    """MacroCalendarProvider over catalysts.json. Research calls get_calendars; R08 reads the snapshot list."""

    def __init__(self, snaps: list):
        self.snaps = list(snaps)

    def get_calendars(self, as_of: datetime):
        return list(self.snaps), ["macro calendars: Oodle pack catalysts.json (source as_of, not pack_built_at)"]


class PackMarket:
    """QuoteProvider + OptionChainProvider + EventProvider + ScreenerProvider over one loaded pack.

    Construction never raises: a failed load is a DataUnavailable on every call (fail closed). Pass
    allow_labels=("EXAMPLE",) only in tests that deliberately exercise EXAMPLE fixtures.
    """

    def __init__(self, res: md.PackResult, *, now: datetime, hold_end: Optional[datetime] = None,
                 macro=None, risk_market_hours=None, allow_labels: tuple = ("MARKET",),
                 skew_seconds: float = md.DEFAULT_SKEW_SECONDS):
        self.res, self.now = res, now
        self.hold_end, self.macro = hold_end, macro
        self.macro_provider = PackMacroCalendars(macro or [])
        self.risk_market_hours = risk_market_hours
        self.allow_labels, self.skew_seconds = allow_labels, skew_seconds
        self.iv_store = None
        self.notes: list[str] = []
        if not res.ok:
            self.notes.append(f"pack not ok: {md.error_id(res.errors[0]) if res.errors else 'unknown'}")

    @classmethod
    def load(cls, root, date: str, *, now: datetime, **kw) -> "PackMarket":
        return cls(md.load_date(root, date, now=now, spec=kw.pop("spec", None)), now=now, **kw)

    def _history_dates(self, ticker: str) -> list[date]:
        store = self.iv_store
        if store is None:
            return []
        out = []
        for o in store.load(ticker):
            try:
                out.append(date.fromisoformat(str(o["date"])))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def reasons(self, ticker: str, hold_end: Any = _USE_PACK_HOLD) -> list[str]:
        """R07/R08 use the pack hold end unless the caller passes a datetime or an explicit None (skip)."""
        end = self.hold_end if hold_end is _USE_PACK_HOLD else hold_end
        return mdfeeds.decision_reasons(
            self.res, ticker, now=self.now, hold_end=end,
            macro=self.macro if self.macro is not None else [],
            risk_market_hours=self.risk_market_hours, skew_seconds=self.skew_seconds,
            allow_labels=self.allow_labels, iv_history_dates=self._history_dates(ticker))

    def usable(self, ticker: str, hold_end: Any = _USE_PACK_HOLD) -> bool:
        return not self.reasons(ticker, hold_end=hold_end)

    def _snap(self, symbol: str) -> dict:
        d = self.res.snapshots.get(symbol.upper()) if self.res.ok else None
        if not isinstance(d, dict):
            raise DataUnavailable(f"pack: {symbol} not in the pack")
        return d

    def _quote_is_fresh(self, f) -> bool:
        """True when the field already passed the pack staleness window at self.now. Research must not then
        mark it stale solely because a pack quote is not a Nasdaq realtime flag."""
        if not md.field_usable(f) or self.res.quote_limit is None or not isinstance(self.now, datetime) \
                or self.now.tzinfo is None:
            return False
        t = _as_of(f)
        if t is None or t > self.now:
            return False
        return (self.now - t) <= self.res.quote_limit

    def get_quote(self, symbol: str) -> UnderlyingQuote:
        if not self.res.ok:
            raise DataUnavailable(f"pack not ok ({md.error_id(self.res.errors[0]) if self.res.errors else '?'})")
        sym = symbol.upper()
        why = self.reasons(sym)
        if why:
            raise DataUnavailable(f"pack: {sym} NO IDEA: {md.error_id(why[0])}"
                                  + (f" (+{len(why) - 1} more)" if len(why) > 1 else ""))
        d = self._snap(sym)
        lp = d.get("last_price")
        prior, _ = mdfeeds.prior_close(self.res, sym, now=self.now)
        return UnderlyingQuote(
            symbol=sym, last=_val(lp), quote_time=_as_of(lp),
            prev_close=_val(prior) if prior else None, prev_close_time=_as_of(prior) if prior else None,
            is_realtime=self._quote_is_fresh(lp), source=f"urn:optpipe:pack:{self.res.date}", fetched_at=self.now,
            market_status=None)

    def get_chain(self, symbol: str, quote: Optional[UnderlyingQuote] = None) -> OptionChain:
        """Only contracts that pass every CONTRACT_CRITICAL field (usable_contracts); empty when the ticker is
        NO IDEA. Research / the planner must not invent legs from anything else (Moodle to-do c)."""
        if not self.res.ok:
            raise DataUnavailable(f"pack not ok ({md.error_id(self.res.errors[0]) if self.res.errors else '?'})")
        sym = symbol.upper()
        why = self.reasons(sym)
        if why:
            raise DataUnavailable(f"pack: {sym} NO IDEA: {md.error_id(why[0])}"
                                  + (f" (+{len(why) - 1} more)" if len(why) > 1 else ""))
        contracts = md.usable_contracts(self.res, sym, now=self.now, skew_seconds=self.skew_seconds)
        snap = self._snap(sym)
        quotes, expiries = [], set()
        for c in contracts:
            try:
                exp = date.fromisoformat(c["expiry"])
            except (KeyError, TypeError, ValueError):
                continue
            right = _right(c.get("right", "C"))
            bid, ask, oi = c.get("bid"), c.get("ask"), c.get("open_interest")
            t = _as_of(bid) or _as_of(ask) or self.now
            quotes.append(OptionQuote(
                underlying=sym, expiry=exp, strike=float(c["strike"]), right=right,
                bid=_val(bid), ask=_val(ask), open_interest=int(_val(oi)) if _val(oi) is not None else None,
                volume=int(_val(c.get("volume"))) if _val(c.get("volume")) is not None else None,
                iv=_iv_number(c.get("iv")) if isinstance(c.get("iv"), dict) else _iv_number(snap.get("iv")),
                quote_time=t, quote_basis=QuoteBasis.REALTIME))
            expiries.add(exp)
        return OptionChain(underlying=sym, expiries=sorted(expiries), quotes=quotes,
                           quote_basis=QuoteBasis.REALTIME, source=f"urn:optpipe:pack:{self.res.date}",
                           fetched_at=self.now, note="usable_contracts only (PR B)")

    def get_events(self, symbol: str) -> list[Event]:
        if not self.res.ok:
            return []
        sym = symbol.upper()
        out: list[Event] = []
        for c in (self.res.catalysts or {}).get("catalysts") or []:
            if not isinstance(c, dict) or c.get("ticker") not in (sym, None):
                continue
            if c.get("catalyst_type") != "earnings" or c.get("ticker") != sym:
                continue
            try:
                ed = date.fromisoformat(c["event_date"])
            except (KeyError, TypeError, ValueError):
                continue
            timing = EventTiming.UNKNOWN
            # prefer the snapshot's earnings_time when usable
            snap = self.res.snapshots.get(sym) or {}
            et = (snap.get("earnings_time") or {}).get("value") if isinstance(snap, dict) else None
            if et == "BMO":
                timing = EventTiming.BMO
            elif et == "AMC":
                timing = EventTiming.AMC
            out.append(Event(kind=EventKind.EARNINGS, date=ed, symbol=sym, timing=timing,
                             description=c.get("note") or f"earnings {c.get('status')}",
                             source=f"urn:optpipe:pack:{self.res.date}/catalysts.json"))
        return out

    def get_screen(self, screen: str) -> ScreenResult:
        """Pack index tickers: screen name is 'pack_<watchlist>' (e.g. pack_eugene) or 'pack' for every ticker."""
        if not self.res.ok:
            return ScreenResult(screen=screen, status="error", source=f"urn:optpipe:pack:{self.res.date}",
                                note=md.error_id(self.res.errors[0]) if self.res.errors else "pack not ok")
        want = None if screen in ("pack", "pack_all") else screen.removeprefix("pack_")
        rows = []
        for ent in (self.res.index or {}).get("tickers") or []:
            if not isinstance(ent, dict):
                continue
            if want and ent.get("watchlist") != want:
                continue
            t = ent.get("ticker")
            if not t:
                continue
            d = self.res.snapshots.get(t) or {}
            rows.append(ScreenRow(ticker=t, price=_val(d.get("last_price"))))
        return ScreenResult(screen=screen, status="ok" if rows else "empty", rows=rows,
                            source=f"urn:optpipe:pack:{self.res.date}", fetched_at=self.now)


def pack_macro_from_catalysts(res: md.PackResult, *, now: datetime) -> list[CalendarSnapshot]:
    """Build CalendarSnapshot list from catalysts.json macro entries (FOMC / CPI / PPI / NFP) for R08.

    Coverage = the latest event_date of that kind on or after the pack date (fail closed: no events -> no snap).
    fetched_at = pack_built_at (the pack is the source of truth for this run).
    """
    if not res.ok or not isinstance(res.catalysts, dict):
        return []
    by: dict[str, list] = {k: [] for k in mdfeeds.MACRO_KINDS}
    reads: dict[str, list] = {k: [] for k in mdfeeds.MACRO_KINDS}
    for c in res.catalysts.get("catalysts") or []:
        if not isinstance(c, dict) or c.get("ticker") is not None:
            continue
        kind = _macro_kind(c)
        if kind not in by:
            continue
        try:
            ed = date.fromisoformat(c["event_date"])
            at = md._aware(c["event_time"]) if c.get("event_time") else datetime.combine(ed, datetime.min.time())
        except (KeyError, TypeError, ValueError):
            continue
        if at.tzinfo is None:
            from zoneinfo import ZoneInfo
            at = at.replace(tzinfo=ZoneInfo("America/New_York"))
        by[kind].append(MacroEvent(kind=kind, at=at, description=c.get("note") or kind,
                                   note=c.get("status")))
        reads[kind].extend(_source_read_times(c))
    out = []
    for kind, evs in by.items():
        if not evs:
            continue
        times = reads[kind]
        future = [t for t in times if t > now]
        # age is the source read, never pack_built_at. No usable as_of → macro_coverage says the read is unusable.
        # Any future read is reported as future. Otherwise the oldest read fails the 7-day check if one source is stale.
        if not times:
            fetched = ""
        elif future:
            fetched = min(future).isoformat()
        else:
            fetched = min(times).isoformat()
        last = max(e.at.date() for e in evs)
        out.append(CalendarSnapshot(
            kind=kind,
            source={"id": f"src-pack-{kind.lower()}", "url": f"urn:optpipe:pack:{res.date}/catalysts.json",
                    "kind": "economic_calendar", "fetched_at": fetched, "publisher": "Oodle pack"},
            events=sorted(evs, key=lambda e: e.at), checked_through=last,
            formula=f"pack catalysts.json {kind} through {last.isoformat()}"))
    return out


def load_pack_market(root, date: str, *, now: datetime, hold_end: Optional[datetime] = None,
                     risk_market_hours=None, allow_labels: tuple = ("MARKET",),
                     spec=None) -> PackMarket:
    """Load the pack for `date` under `root` and wrap it. Macro calendars are derived from catalysts.json."""
    res = md.load_date(root, date, now=now, spec=spec)
    macro = pack_macro_from_catalysts(res, now=now)
    return PackMarket(res, now=now, hold_end=hold_end, macro=macro,
                      risk_market_hours=risk_market_hours, allow_labels=allow_labels)
