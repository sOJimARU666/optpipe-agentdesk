"""optpipe.mdfeeds: per-feed freshness (MD-19), calendar coverage and R07 / R08 checks on a loaded market-data pack
(FEAT-OODLE-01 PR B). PAPER ONLY, pure functions, no network, no file writes, and NO wall clock: every check takes the
injected decision time `now` (aware); a naive or missing clock is NO IDEA.

Every id below is one Doodle PROPOSED (trading/proposals/R7-acceptance-proposal.md, MD-16..MD-21; proposal only, not
in spec 1.1.8), so every returned string carries "(proposed)" (marketdata.label_ids). Decision times D = `now`,
build time B = index.pack_built_at; all calendar logic is in America/New_York (optpipe.nyse_calendar).

  MD-19-CALENDAR       D's or B's NY date is outside the app calendar's coverage (2026-01-01..2027-12-31), a hold
                       end cannot be placed on a session, or the spec risk calendar disagrees with the app calendar
                       on D's date (proposal Q-5: the risk calendar knows 2026 only)
  MD-19-STALE-DAY      B's NY date is not D's NY date: quotes, prior_close and street ideas are bound to B's date
  MD-19-STALE-STREET   a street-watchlist ticker used on another NY date than B's (the idea expires with the day)
  MD-19-STALE-PRIOR    prior_close not usable, or not from the last session completed at both B and D
  MD-19-STALE-EARNINGS an earnings_date / earnings_time source read more than 1 session before B, or (event at most
                       2 sessions after B) not read on B's NY date
  MD-19-PAST-EARNINGS  earnings_date before D's NY date: the next earnings date is unknown
  MD-19-STALE-MACRO    a macro calendar read more than 7 days before B or D
  MD-19-FUTURE         a macro calendar read after D
  MD-19-LOOKAHEAD      an IV-history observation after the last session completed at D
  MD-19-STALE-HISTORY  the last IV-history observation is not the last session completed at D
  MD-20-CRITICAL R07   earnings coverage (through the next CONFIRMED earnings date E) ends before the hold-window end
  MD-20-CRITICAL R08   a macro kind (FOMC / CPI / PPI / NFP) without a usable calendar, or coverage ending before the
                       hold-window end

API
  decision_reasons(res, ticker, *, now, hold_end=None, macro=None, risk_market_hours=None, ...) -> list[str]
  earnings_coverage(res, ticker, *, now) -> Optional[date]      # E, the date earnings coverage runs through
  macro_coverage(macro, *, now, built) -> (Optional[date], list[str])
  prior_close(res, ticker, *, now) -> (field | None, reason | None)
  history_reasons(dates, *, now) -> list[str]
  hold_end_for(expiry) -> Optional[datetime]                     # close of the expiry session (app calendar)
  select_pack(root, t, **kw) -> PackResult                       # replay: the pack for NY date(t), loaded with now=t
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from . import nyse_calendar as cal
from .marketdata import (DEFAULT_SKEW_SECONDS, PackResult, _aware, field_usable, label_ids, load_date,
                         no_idea_reasons)

MACRO_KINDS = ("FOMC", "CPI", "PPI", "NFP")
MACRO_MAX_AGE = timedelta(days=7)
EARNINGS_MAX_SESSIONS = 1          # an earnings source may be read at most 1 session before B
EARNINGS_NEAR_SESSIONS = 2         # an event at most 2 sessions after B must be re-read on B's date


def _aware_now(now) -> bool:
    return isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None


def built_at(res: PackResult) -> Optional[datetime]:
    return _aware((res.index or {}).get("pack_built_at") or "") if isinstance(res.index, dict) else None


def _ny_date(t: datetime) -> date:
    return t.astimezone(cal.NY).date()


def calendar_reasons(res: PackResult, *, now: datetime, risk_market_hours=None) -> list[str]:
    b = built_at(res)
    out = []
    for what, t in (("D (decision time)", now), ("B (pack_built_at)", b)):
        if t is None or not cal.covers(_ny_date(t)):
            out.append(f"MD-19-CALENDAR {what} {t.isoformat() if t else 'missing'}: NY date outside the app calendar "
                       f"{cal.VERSION} coverage {cal.COVERS_FROM}..{cal.COVERS_TO} (UNKNOWN: NO IDEA)")
    if risk_market_hours is not None and not out:
        d = _ny_date(now)
        try:
            from .risk.engine import Calendar
            rc = Calendar(risk_market_hours)
            same = bool(rc.is_trading_day(d)) == bool(cal.is_session(d)) and \
                (not cal.is_session(d) or rc.close_dt(d) == cal.close_at(d))
        except Exception:  # noqa: BLE001 - fail closed
            same = False
        if not same:
            out.append(f"MD-19-CALENDAR {d}: the spec risk calendar (risk_rules.yaml market_hours) and the app calendar "
                       f"{cal.VERSION} disagree on this date (Q-5; NO IDEA)")
    return out


def day_reasons(res: PackResult, ticker: str, *, now: datetime) -> list[str]:
    b = built_at(res)
    if b is None:
        return [f"MD-19-STALE-DAY {ticker}: pack_built_at unusable"]
    if _ny_date(b) == _ny_date(now):
        return []
    out = [f"MD-19-STALE-DAY {ticker}: pack built on NY date {_ny_date(b)}, decision on {_ny_date(now)}"]
    ent = next((e for e in (res.index or {}).get("tickers") or [] if isinstance(e, dict) and e.get("ticker") == ticker),
               None)
    if ent and ent.get("watchlist") == "street":
        out.append(f"MD-19-STALE-STREET {ticker}: a street idea is valid on its pack's NY date {_ny_date(b)} only")
    return out


def _ctx(res: PackResult, ticker: str) -> Optional[dict]:
    for c in (res.catalysts or {}).get("ticker_context") or [] if isinstance(res.catalysts, dict) else []:
        if isinstance(c, dict) and c.get("ticker") == ticker:
            return c
    return None


def prior_close(res: PackResult, ticker: str, *, now: datetime) -> tuple[Optional[dict], Optional[str]]:
    """(the catalysts.json ticker_context prior_close field, None) when it is usable and is the regular close of
    the last session completed at both B and D; else (None, MD-19-STALE-PRIOR reason)."""
    b = built_at(res)
    c = _ctx(res, ticker)
    f = c.get("prior_close") if isinstance(c, dict) else None
    if f is None:
        return None, f"MD-19-STALE-PRIOR {ticker}.prior_close: not in the pack"
    if not field_usable(f) or b is None or not _aware_now(now):
        return None, f"MD-19-STALE-PRIOR {ticker}.prior_close: status {f.get('status') if isinstance(f, dict) else '?'}"
    t = _aware(f.get("as_of") or "")
    want_b, want_d = cal.last_completed_session(b), cal.last_completed_session(now)
    if want_b is None or want_d is None:
        return None, f"MD-19-CALENDAR {ticker}.prior_close: the last completed session is outside the app calendar"
    if want_b != want_d:
        return None, (f"MD-19-STALE-PRIOR {ticker}.prior_close: the session {want_b} closed before B, but {want_d} "
                      "closed before D")
    close = cal.close_at(want_b)
    # the print is the official close, not any timestamp on that session's calendar date
    if t is None or close is None or t != close:
        return None, (f"MD-19-STALE-PRIOR {ticker}.prior_close: as_of {f.get('as_of')} is not the {want_b} regular "
                      f"close ({close.isoformat() if close else 'unknown'})")
    return f, None


def _source_times(f: dict) -> list[Optional[datetime]]:
    src = f.get("sources") if isinstance(f.get("sources"), list) and f.get("sources") else [f]
    return [_aware(s.get("as_of") or "") if isinstance(s, dict) else None for s in src]


def earnings_reasons(res: PackResult, ticker: str, *, now: datetime) -> list[str]:
    d = res.snapshots.get(ticker) if isinstance(res.snapshots, dict) else None
    b = built_at(res)
    if not isinstance(d, dict) or b is None:
        return []          # no_idea_reasons already says why
    out = []
    ed = d.get("earnings_date")
    e = None
    if field_usable(ed):
        try:
            e = date.fromisoformat(ed["value"])
        except (TypeError, ValueError):
            e = None
    if e is not None and e < _ny_date(now):
        out.append(f"MD-19-PAST-EARNINGS {ticker}.earnings_date: {e} is before the decision date {_ny_date(now)} "
                   "(the next earnings date is unknown)")
    bd = _ny_date(b)
    near = e is not None and (n := cal.sessions_after(bd, e)) is not None and n <= EARNINGS_NEAR_SESSIONS
    for k in ("earnings_date", "earnings_time"):
        f = d.get(k)
        if not field_usable(f):
            continue
        for t in _source_times(f):
            if t is None:
                out.append(f"MD-19-STALE-EARNINGS {ticker}.{k}: a source has no usable as_of")
                continue
            age = cal.sessions_after(_ny_date(t), bd) if _ny_date(t) <= bd else 0
            if age is None:
                out.append(f"MD-19-CALENDAR {ticker}.{k}: source as_of {t.isoformat()} outside the app calendar")
            elif age > EARNINGS_MAX_SESSIONS:
                out.append(f"MD-19-STALE-EARNINGS {ticker}.{k}: source read {t.isoformat()}, {age} sessions before B "
                           f"(max {EARNINGS_MAX_SESSIONS})")
            elif near and _ny_date(t) != bd:
                out.append(f"MD-19-STALE-EARNINGS {ticker}.{k}: the event {e} is at most {EARNINGS_NEAR_SESSIONS} "
                           f"sessions after B, but a source was read {t.isoformat()}, not on B's date {bd}")
    return sorted(set(out))


def earnings_coverage(res: PackResult, ticker: str, *, now: datetime) -> Optional[date]:
    """E: the usable (two-origin OK, corroborated, MD-20-checked) next CONFIRMED earnings date; earnings coverage is
    known through E only (what follows E is not in the pack). None when it is not usable."""
    d = res.snapshots.get(ticker) if isinstance(res.snapshots, dict) else None
    ed = d.get("earnings_date") if isinstance(d, dict) else None
    if not field_usable(ed):
        return None
    try:
        e = date.fromisoformat(ed["value"])
    except (TypeError, ValueError):
        return None
    return e if _aware_now(now) and e >= _ny_date(now) else None


def macro_coverage(macro, *, now: datetime, built: Optional[datetime],
                   kinds: Iterable[str] = MACRO_KINDS) -> tuple[Optional[date], list[str]]:
    """(the earliest checked_through over `kinds`, reasons). `macro` = providers.macro CalendarSnapshot list or None.
    A kind with no calendar, an unusable checked_through, a calendar read after D or more than 7 days before B / D
    gives a reason and coverage None."""
    out: list[str] = []
    ends: list[date] = []
    by_kind = {}
    for s in macro or []:
        by_kind.setdefault(getattr(s, "kind", None), []).append(s)
    for k in kinds:
        snaps = by_kind.get(k) or []
        if not snaps:
            out.append(f"MD-20-CRITICAL R08 {k}: no macro calendar for this run (coverage UNKNOWN)")
            continue
        best = None
        for s in snaps:
            try:
                f = s.fetched_at
            except Exception:  # noqa: BLE001
                f = None
            if not _aware_now(f):
                out.append(f"MD-19-STALE-MACRO {k}: calendar read time unusable")
                continue
            if f > now:
                out.append(f"MD-19-FUTURE {k} calendar: read {f.isoformat()}, after D {now.isoformat()}")
                continue
            if now - f > MACRO_MAX_AGE or (built is not None and built - f > MACRO_MAX_AGE):
                out.append(f"MD-19-STALE-MACRO {k} calendar: read {f.isoformat()}, more than 7 days before B / D")
                continue
            if not isinstance(s.checked_through, date):
                out.append(f"MD-20-CRITICAL R08 {k}: calendar has no coverage end")
                continue
            best = s.checked_through if best is None or s.checked_through > best else best
        if best is not None:
            ends.append(best)
    if out or len(ends) != len(tuple(kinds)):
        return None, out
    return min(ends), out


def hold_end_for(expiry: date) -> Optional[datetime]:
    """the official close of the expiry session (the latest instant a hold window can end at); None when the
    expiry is no session or outside the app calendar (UNKNOWN)"""
    return cal.close_at(expiry)


def _earnings_session(res: PackResult, ticker: str) -> Optional[str]:
    """Usable BMO / AMC, else None. A STALE session label is not a placement (no_idea_reasons already says so)."""
    d = res.snapshots.get(ticker) if isinstance(res.snapshots, dict) else None
    et = d.get("earnings_time") if isinstance(d, dict) else None
    if field_usable(et) and et.get("value") in ("BMO", "AMC"):
        return et["value"]
    return None


def r07_reasons(res: PackResult, ticker: str, *, now: datetime, hold_end: datetime) -> list[str]:
    """Earnings coverage ends at the print, not merely on the calendar date E.

    BMO is before the open of E, so a hold that reaches that open includes the print and what follows it is not
    in the pack. AMC is after the close of E, so a hold through that close does not include the print.
    """
    e = earnings_coverage(res, ticker, now=now)
    if e is None:
        return [f"MD-20-CRITICAL R07 {ticker}: no usable next CONFIRMED earnings date (coverage UNKNOWN)"]
    sess = _earnings_session(res, ticker)
    if sess == "BMO":
        if hold_end >= cal.ny_at(e, cal.OPEN):
            return [f"MD-20-CRITICAL R07 {ticker}: earnings {e} BMO is before the open; a hold ending "
                    f"{hold_end.isoformat()} includes the print and the next earnings date is not in the pack"]
        return []
    if sess == "AMC":
        close_e = cal.close_at(e)
        if close_e is None or hold_end > close_e:
            return [f"MD-20-CRITICAL R07 {ticker}: earnings {e} AMC is after the close; the hold window ends "
                    f"{_ny_date(hold_end)} and the next earnings date is not in the pack"]
        return []
    if _ny_date(hold_end) > e:
        return [f"MD-20-CRITICAL R07 {ticker}: earnings coverage runs through {e} (the next CONFIRMED earnings; the "
                f"one after it is not in the pack), the hold window ends {_ny_date(hold_end)}"]
    return []


def r08_reasons(macro, *, now: datetime, built: Optional[datetime], hold_end: datetime) -> list[str]:
    end, out = macro_coverage(macro, now=now, built=built)
    if end is None:
        return out or ["MD-20-CRITICAL R08: macro coverage UNKNOWN"]
    if _ny_date(hold_end) > end:
        return [f"MD-20-CRITICAL R08: macro coverage runs through {end}, the hold window ends {_ny_date(hold_end)}"]
    return []


def history_reasons(dates: Iterable[date], *, now: datetime, what: str = "iv_history") -> list[str]:
    """LOOKAHEAD: no observation after the last session completed at D; STALE-HISTORY: the last observation must be
    that session. [] for an empty history (the caller records it as missing; R10 then says UNKNOWN)."""
    ds = sorted(set(dates))
    if not ds:
        return []
    if not _aware_now(now):
        return label_ids([f"MD-19-TZ {what}: naive clock"])
    last = cal.last_completed_session(now)
    if last is None:
        return label_ids([f"MD-19-CALENDAR {what}: the last completed session at D is outside the app calendar"])
    out = []
    ahead = [d for d in ds if d > last]
    if ahead:
        out.append(f"MD-19-LOOKAHEAD {what}: {len(ahead)} observation(s) after {last}, the last session completed at "
                   f"D (first {ahead[0]})")
    elif ds[-1] != last:
        out.append(f"MD-19-STALE-HISTORY {what}: last observation {ds[-1]}, expected {last}")
    return label_ids(out)


def prospective_hold_end(now: datetime, *, horizon_days: int) -> Optional[datetime]:
    """Official close of the last session on or before `now`'s NY date plus `horizon_days` calendar days.

    That is the far end of the planner's DTE window, so R07 / R08 can gate an idea before a leg is chosen.
    None when the walk leaves the app calendar (UNKNOWN).
    """
    if not _aware_now(now) or not isinstance(horizon_days, int) or isinstance(horizon_days, bool) or horizon_days < 0:
        return None
    target = _ny_date(now) + timedelta(days=horizon_days)
    for _ in range(14):
        if not cal.covers(target):
            return None
        close = cal.close_at(target)
        if close is not None:
            return close
        target -= timedelta(days=1)
    return None


def decision_reasons(res: PackResult, ticker: str, *, now: datetime, hold_end: Optional[datetime] = None,
                     macro=None, risk_market_hours=None, skew_seconds: float = DEFAULT_SKEW_SECONDS,
                     allow_labels: tuple = ("MARKET",), iv_history_dates: Optional[Iterable[date]] = None) -> list[str]:
    """[] only if `ticker` may feed an idea at `now`: marketdata.no_idea_reasons, then the calendar, the NY-date
    binding, the earnings freshness windows, IV-history freshness (`iv_history_dates`) and, with a hold_end, R07
    (earnings coverage, BMO/AMC placement) and R08 (macro calendars, `macro` = CalendarSnapshot list). Anything
    else is NO IDEA (fail closed, never raises). An empty history is missing (R10), not by itself NO IDEA."""
    try:
        out = list(no_idea_reasons(res, ticker, now=now, skew_seconds=skew_seconds, allow_labels=allow_labels))
        if not _aware_now(now) or not res.ok:
            return out
        more = calendar_reasons(res, now=now, risk_market_hours=risk_market_hours)
        more += day_reasons(res, ticker, now=now) + earnings_reasons(res, ticker, now=now)
        more += history_reasons(iv_history_dates or [], now=now, what=f"{ticker}.iv_history")
        if hold_end is not None:
            if not _aware_now(hold_end):
                more.append(f"MD-19-TZ {ticker}: naive hold end")
            else:
                more += r07_reasons(res, ticker, now=now, hold_end=hold_end)
                more += r08_reasons(macro, now=now, built=built_at(res), hold_end=hold_end)
        return out + label_ids(more)
    except Exception as ex:  # noqa: BLE001 - fail closed
        return [f"MD-00-CRASH {ticker}: decision_reasons raised {type(ex).__name__} (NO IDEA)"]


def select_pack(root, t: datetime, **kw) -> PackResult:
    """Replay selection: the pack for t's America/New_York date, loaded with now=t (so a pack built after t is
    MD-19-FUTURE-PACK and fails). Never reads the wall clock; a naive t is MD-19-TZ (not loaded)."""
    if not _aware_now(t):
        return PackResult(ok=False, errors=label_ids(["MD-19-TZ select_pack: naive replay time (fail closed)"]))
    return load_date(root, _ny_date(t).isoformat(), now=t, **kw)
