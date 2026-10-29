"""Watcher runtime (M3). PAPER ONLY / ALERT ONLY. Owner: Moodle (architecture) / Poodle (code).

Loads the session's HYPOTHETICAL tickets and polls quotes only while the NY regular session is open. It
runs a pluggable rule engine (default: the spec engine in watcher/engine.py, i.e. Doodle's
watcher_rules.yaml) and writes <session>.watch.json plus a .sha256 sidecar.

  * Targets: each ticket gets a status derived from the record.
      - vetoed:   latest risk review is VETO, or the idea_log says vetoed. This is a SHADOW target, flagged
                  vetoed=True: no intraday alerts, one end-of-day summary.
      - rejected: SHADOW target.
      - approved: the approval says approved. Watched as approved_pending.
      - neither:  HYPOTHETICAL (no approval recorded). Watched as approved_pending and flagged
                  hypothetical=True, which is a paper what-if.
      - open paper_positions are watched as kind=position.
  * Gating: quotes are fetched only while Calendar.is_open(t). Premarket, after close, weekends, holidays
    and the time after an early close never reach the quote source. The shadow end-of-day mark is sampled
    at the last in-session wake (close - shadow_sample_minutes), so the after-close summary needs no data.
  * Quote sources are GET-only: ReplayTape (fixtures, no network) or LiveNasdaqSource (read-only GETs via
    HttpFetcher, which has no POST/PUT/DELETE method).
  * Output: nothing is sent anywhere. No order path, no notifications, no outside service is contacted
    except the optional read-only quote GETs in --live mode.
"""
from __future__ import annotations

import hashlib
import json
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

import yaml

from ..risk.engine import Calendar
from . import engine as spec_engine
from .engine import BERLIN, NY, SHADOW, mark_of

WATCH_VERSION = "watch-1.0"
LABEL = "PAPER ONLY - alert only, no order. Nothing here places, changes or cancels an order or notifies anyone."
SHADOW_SAMPLE_MINUTES = 5


def _ts(s) -> datetime:
    dt = s if isinstance(s, datetime) else datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp forbidden: {s}")
    return dt


def _flt(x):
    return None if x is None else float(x)


def pair(dt: datetime) -> dict:
    return {"ny": dt.astimezone(NY).isoformat(), "berlin": dt.astimezone(BERLIN).isoformat()}


def _ny(v) -> str:
    return v["ny"] if isinstance(v, dict) else str(v)


# ------------------------------------------------------------------ pluggable rule engines
class RuleEngine(Protocol):
    name: str

    def poll(self, inp: dict, state: Optional[dict], rules: dict, cal: Calendar) -> tuple[list[dict], dict, Optional[int]]: ...


@dataclass
class SpecEngine:
    """Default engine: watcher/engine.py, the parity port of Doodle's watcher_reference.py."""
    name: str = "spec"

    def poll(self, inp, state, rules, cal):
        return spec_engine.poll(inp, state, rules, cal)


ENGINES: dict[str, Callable[[], RuleEngine]] = {"spec": SpecEngine}


def register_engine(name: str, factory: Callable[[], RuleEngine]) -> None:
    """Plug in another rule engine. It must be pure and alert only (enforced by tests/test_paper_only.py)."""
    ENGINES[name] = factory


def get_engine(name: str) -> RuleEngine:
    if name not in ENGINES:
        raise KeyError(f"unknown watcher engine {name!r}; known: {sorted(ENGINES)}")
    return ENGINES[name]()


# ------------------------------------------------------------------ market gating
def market_state(t: datetime, cal: Calendar) -> tuple[bool, str]:
    """(open?, reason): open | premarket | after_close | early_close | weekend | holiday."""
    n = t.astimezone(NY)
    d = n.date()
    if d.weekday() >= 5:
        return False, "weekend"
    if not cal.is_trading_day(d):
        return False, "holiday"
    if cal.is_open(t):
        return True, "open"
    if n < datetime.combine(d, cal.open, NY):
        return False, "premarket"
    return False, ("early_close" if d in cal.early_closes else "after_close")


def open_dt(cal: Calendar, d: date) -> datetime:
    return datetime.combine(d, cal.open, NY)


def next_open(cal: Calendar, t: datetime) -> datetime:
    d = t.astimezone(NY).date()
    for i in range(0, 15):
        dd = d + timedelta(days=i)
        if cal.is_trading_day(dd):
            o = open_dt(cal, dd)
            if o > t:
                return o
    raise ValueError(f"no trading day within 15 days of {t}")


# ------------------------------------------------------------------ quote sources (GET-only)
class QuoteSource(Protocol):
    name: str
    method: str
    calls: list

    def snapshot(self, now: datetime, symbols: list[str], contracts: list[str]) -> dict: ...


def _f(value, as_of: Optional[datetime], source: str, status: str = "ok") -> dict:
    if value is None:
        return {"value": None, "status": "missing", "as_of": None, "source_id": source}
    return {"value": value, "status": status, "as_of": as_of.isoformat() if as_of else None, "source_id": source}


class ReplayTape:
    """Hand-made / recorded quote tape. snapshot(now) returns the latest tick with at <= now (never the
    future), so a gap in the tape shows up as stale quotes (W06). bars_5m are derived from consecutive
    ticks. No network."""
    name, method = "replay", "GET"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = yaml.safe_load(self.path.read_text())
        self.label = raw.get("label", "")
        self.ticks = sorted(({**t, "_at": _ts(t["at"])} for t in raw["ticks"]), key=lambda t: t["_at"])
        self.calls: list[dict] = []

    def snapshot(self, now, symbols, contracts):
        self.calls.append({"method": "GET", "at": now.isoformat(), "symbols": list(symbols)})
        past = [t for t in self.ticks if t["_at"] <= now]
        src = f"src-replay-{self.path.stem}"
        if not past:
            return {"underlyings": {s: {"last": _f(None, None, src), "bars_5m": []} for s in symbols},
                    "options": {c: {"bid": _f(None, None, src), "ask": _f(None, None, src), "iv": _f(None, None, src)}
                                for c in contracts}}
        tk = past[-1]
        und = {}
        for s in symbols:
            v = (tk.get("underlyings") or {}).get(s)
            bars = [{"end": b["_at"].isoformat(), "close": b["underlyings"][s]}
                    for b in past[-13:] if s in (b.get("underlyings") or {})]
            und[s] = {"last": _f(v, tk["_at"], src), "bars_5m": bars}
        opts = {}
        for c in contracts:
            q = (tk.get("options") or {}).get(c)
            b, a, iv = (q + [None, None, None])[:3] if q else (None, None, None)
            opts[c] = {"bid": _f(b, tk["_at"], src), "ask": _f(a, tk["_at"], src), "iv": _f(iv, tk["_at"], src)}
        return {"underlyings": und, "options": opts}


class LiveNasdaqSource:
    """EXPERIMENTAL live quotes: read-only HTTP GETs to Nasdaq's public quote/chain endpoints through
    HttpFetcher (GET only). Only called while the market is open."""
    name, method = "nasdaq-live", "GET"

    def __init__(self, user_agent: str, timeout_s: float = 15.0):
        from ..providers.fetchers import HttpFetcher
        from ..providers.nasdaq import NasdaqProvider
        self.provider = NasdaqProvider(HttpFetcher(user_agent, timeout_s))
        self.calls: list[dict] = []

    def snapshot(self, now, symbols, contracts):
        self.calls.append({"method": "GET", "at": now.isoformat(), "symbols": list(symbols)})
        und, opts, want = {}, {}, set(contracts)
        for s in symbols:
            q = self.provider.get_quote(s)
            und[s] = {"last": _f(q.last, q.quote_time, f"src-nasdaq-{s.lower()}-live"), "bars_5m": []}
            ch = self.provider.get_chain(s, q)
            for oq in ch.quotes:
                cid = f"{s}{oq.expiry:%y%m%d}{'C' if str(oq.right.value if hasattr(oq.right, 'value') else oq.right).upper().startswith('C') else 'P'}{int(round(oq.strike * 1000)):08d}"
                if cid in want:
                    src = f"src-nasdaq-{s.lower()}-chain-live"
                    opts[cid] = {"bid": _f(oq.bid, oq.quote_time, src), "ask": _f(oq.ask, oq.quote_time, src),
                                 "iv": _f(oq.iv, oq.quote_time, src)}
        for c in want - set(opts):
            opts[c] = {"bid": _f(None, None, "src-nasdaq"), "ask": _f(None, None, "src-nasdaq"), "iv": _f(None, None, "src-nasdaq")}
        return {"underlyings": und, "options": opts}


# ------------------------------------------------------------------ session -> targets
@dataclass
class Target:
    target_id: str
    kind: str                 # ticket | position
    ticket_id: str
    status: str               # engine status: approved_pending | open | vetoed | rejected
    record_status: str        # what the record says (idea_log / review / approval)
    vetoed: bool
    hypothetical: bool
    ticket: dict
    note: str = ""
    state: dict = field(default_factory=dict)
    prev_last: Optional[float] = None
    shadow_sample: Optional[dict] = None

    @property
    def shadow(self) -> bool:
        return self.status in SHADOW

    def header(self) -> dict:
        t = self.ticket
        return {"target_id": self.target_id, "kind": self.kind, "ticket_id": self.ticket_id, "status": self.status,
                "record_status": self.record_status, "vetoed": self.vetoed, "hypothetical": self.hypothetical,
                "watched_intraday": not self.shadow, "underlying": t["underlying"], "strategy": t.get("strategy"),
                "note": self.note}


def _latest_review(rec: dict, tid: str) -> Optional[dict]:
    rv = [r for r in rec.get("risk_reviews") or [] if r["ticket_id"] == tid]
    return rv[-1] if rv else None


def targets_from_session(rec: dict) -> list[Target]:
    ideas = {i.get("ticket_id"): i for i in rec.get("idea_log") or []}
    approvals: dict[str, dict] = {}
    for a in rec.get("approvals") or []:
        approvals[a.get("ticket_id")] = a
    out = []
    for tk in rec.get("tickets") or []:
        tid = tk["ticket_id"]
        idea = ideas.get(tid) or {}
        rv = _latest_review(rec, tid)
        verdict = (rv or {}).get("overall", {}).get("verdict")
        istat = idea.get("current_status", "proposed")
        appr = approvals.get(tid)
        if verdict == "VETO" or istat == "vetoed":
            why = ", ".join((rv or {}).get("overall", {}).get("blocking_rule_ids") or []) or "idea_log vetoed"
            out.append(Target(tid, "ticket", tid, "vetoed", istat, True, False, tk,
                              f"VETOED ({why}): shadow only, end-of-day summary, no intraday alerts"))
        elif istat == "rejected" or (appr and appr.get("decision") == "rejected"):
            out.append(Target(tid, "ticket", tid, "rejected", istat, False, False, tk, "rejected: shadow only"))
        elif appr and appr.get("decision") in ("approved", "approve"):
            out.append(Target(tid, "ticket", tid, "approved_pending", istat, False, False, tk,
                              "approved for paper; watched as approved_pending"))
        else:
            out.append(Target(tid, "ticket", tid, "approved_pending", istat, False, True, tk,
                              f"HYPOTHETICAL (record status {istat!r}, verdict {verdict or 'none'}, no approval): "
                              "watched as a paper what-if"))
    tickets = {t["ticket_id"]: t for t in rec.get("tickets") or []}
    for p in rec.get("paper_positions") or []:
        if p.get("status") == "open" and p.get("ticket_id") in tickets:
            pid = p.get("position_id") or f"pos-{p['ticket_id']}"
            out.append(Target(pid, "position", p["ticket_id"], "open", "open", False, False, tickets[p["ticket_id"]],
                              "open PAPER position"))
    return out


def _conds(rules: list[dict], metric: str) -> list[dict]:
    return [c for r in rules or [] for c in r.get("conditions") or [] if c.get("metric") == metric]


def mark_levels(tk: dict) -> dict:
    stop = _conds(tk.get("stop_rules"), "position_value")
    tgt = _conds(tk.get("target_rules"), "position_value")
    return {"entry": str(tk["limit_price"]["amount"]),
            "stop": str(stop[0]["value"]) if stop else None,
            "target": str(tgt[0]["value"]) if tgt else None,
            # levels are already tick-rounded per leg rules in the record (stop up, target down); 0.01 keeps them as-is
            "tick_size": "0.01"}


def underlying_levels(tk: dict) -> dict:
    lv: dict[str, Any] = {"stop_below": None, "stop_above": None,
                          "breakevens": [b["underlying_price"] for b in tk.get("breakevens") or []]}
    for c in _conds(tk.get("stop_rules"), "underlying_close"):
        if c["op"] in ("lt", "lte"):
            lv["stop_below"] = c["value"]
        elif c["op"] in ("gt", "gte"):
            lv["stop_above"] = c["value"]
    return lv


def session_events(rec: dict) -> tuple[list[dict], Optional[dict], dict[str, Optional[dict]]]:
    evs, exd = [], {}
    for e in (rec.get("event_calendar") or {}).get("events") or []:
        sa = e.get("scheduled_at") or {}
        if sa.get("status") not in ("ok", "stale") or sa.get("value") is None:
            continue
        evs.append({"event_id": e["event_id"], "kind": e["kind"], "underlying": e.get("underlying"), "at": sa["value"]})
        if e["kind"] == "ex_dividend" and e.get("underlying"):
            amt = (e.get("dividend_amount") or {}).get("value")
            if amt is not None:
                exd[e["underlying"]] = {"date": _ts(sa["value"]).astimezone(NY).date().isoformat(), "amount": str(amt)}
    return evs, None, exd


def build_input(tg: Target, now: datetime, quotes: dict, events: list[dict], exdivs: dict) -> dict:
    tk = tg.ticket
    sym = tk["underlying"]
    u = (quotes.get("underlyings") or {}).get(sym) or {"last": _f(None, None, "none"), "bars_5m": []}
    legs = []
    for i, l in enumerate(tk["legs"], 1):
        q = (quotes.get("options") or {}).get(l["contract_id"]) or {}
        legs.append({"leg_id": f"leg-{i}", "contract_id": l["contract_id"], "type": l["type"], "side": l["side"],
                     "strike": l["strike"], "expiry": str(l["expiry"]), "ratio": l.get("ratio", 1),
                     "bid": q.get("bid") or _f(None, None, "none"), "ask": q.get("ask") or _f(None, None, "none"),
                     "iv": q.get("iv") or _f(None, None, "none"), "entry_iv": l.get("entry_iv")})
    hw = tk["hold_window"]
    return {"now": now.isoformat(),
            "target": {"kind": tg.kind, "id": tg.target_id, "ticket_id": tg.ticket_id, "status": tg.status},
            "premium": "long" if tk["limit_price"]["price_type"] == "debit" else "short",
            "mark_levels": mark_levels(tk),
            "hold_window": {"start": _ny(hw["start"]), "end": _ny(hw["end"])},
            "time_stop": _ny(hw["end"]),
            "underlying": {"symbol": sym, "last": u["last"], "prev_last": tg.prev_last, "bars_5m": u.get("bars_5m") or [],
                           "levels": underlying_levels(tk)},
            "legs": legs, "ex_dividend": exdivs.get(sym), "events": events}


# ------------------------------------------------------------------ clocks
class SimClock:
    """--clock / --replay: simulated time, jumps instantly."""
    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def sleep_until(self, t: datetime) -> None:
        self.t = t


class RealClock:
    def now(self) -> datetime:
        return datetime.now(NY)

    def sleep_until(self, t: datetime) -> None:
        d = (t - self.now()).total_seconds()
        if d > 0:
            _time.sleep(d)


# ------------------------------------------------------------------ the loop
@dataclass
class WatchResult:
    doc: dict
    json_path: Optional[Path] = None
    sha256: Optional[str] = None


def _rel(p) -> str:
    """Path for the watch document: relative to the working directory when inside it (no home/temp paths
    leak into committed snapshots), otherwise just the file name."""
    pp = Path(p).resolve()
    try:
        return pp.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return pp.name


class Watcher:
    def __init__(self, rec: dict, rules: dict, cal: Calendar, source: QuoteSource,
                 engine: Optional[RuleEngine] = None, session_path: Optional[Path] = None):
        spec_engine.check_rules(rules)
        self.rec, self.rules, self.cal, self.source = rec, rules, cal, source
        self.engine = engine or SpecEngine()
        self.session_path = session_path
        self.targets = targets_from_session(rec)
        self.events, _, self.exdivs = session_events(rec)
        self.alerts: list[dict] = []
        self.wakes: list[dict] = []
        self.notes: list[str] = []
        self._sampled: set[str] = set()
        if not any(not t.shadow for t in self.targets):
            self.notes.append("no watched targets: every ticket is vetoed/rejected (shadow only); quotes are fetched "
                              "once per session day for the shadow end-of-day mark")
        if any(t.hypothetical for t in self.targets):
            self.notes.append("HYPOTHETICAL targets have no approval in the record; their alerts are paper what-ifs")
        if any(_conds(t.ticket.get("target_rules"), "underlying_last") for t in self.targets):
            self.notes.append("underlying_last target conditions are not a watcher rule in watcher_rules v1.1.0 "
                              "(W02 watches stop levels and breakevens only)")

    # -- helpers
    def _symbols(self, tgs) -> list[str]:
        return sorted({t.ticket["underlying"] for t in tgs})

    def _contracts(self, tgs) -> list[str]:
        return sorted({l["contract_id"] for t in tgs for l in t.ticket["legs"]})

    def _sample_at(self, d: date) -> datetime:
        return self.cal.close_dt(d) - timedelta(minutes=SHADOW_SAMPLE_MINUTES)

    def _emit(self, tg: Target, alerts: list[dict]) -> None:
        for a in alerts:
            self.alerts.append({**a, "ticket_id": tg.ticket_id, "underlying": tg.ticket["underlying"],
                                "vetoed": tg.vetoed, "hypothetical": tg.hypothetical, "target_status": tg.status})

    # -- one wake
    def step(self, now: datetime) -> Optional[datetime]:
        """Process one wake at `now`; return the next wake time (None = nothing left to do today or later)."""
        is_open, why = market_state(now, self.cal)
        d = now.astimezone(NY).date()
        wake = {"at": pair(now), "market": why, "quote_calls": 0, "polled": [], "alerts": 0}
        watched = [t for t in self.targets if not t.shadow]
        shadows = [t for t in self.targets if t.shadow]
        nxt: Optional[datetime]
        if is_open:
            sample = bool(shadows) and now >= self._sample_at(d) and d.isoformat() not in self._sampled
            need = watched + (shadows if sample else [])
            n_alerts = len(self.alerts)
            if need:
                q = self.source.snapshot(now, self._symbols(need), self._contracts(need))
                wake["quote_calls"] = 1
                steps = []
                for tg in watched:
                    inp = build_input(tg, now, q, self.events, self.exdivs)
                    alerts, tg.state, step = self.engine.poll(inp, tg.state, self.rules, self.cal)
                    self._emit(tg, alerts)
                    last = inp["underlying"]["last"]
                    if last.get("value") is not None:
                        tg.prev_last = last["value"]
                    wake["polled"].append(tg.target_id)
                    if step:
                        steps.append(step)
                if sample:
                    self._sampled.add(d.isoformat())
                    for tg in shadows:
                        inp = build_input(tg, now, q, self.events, self.exdivs)
                        tg.shadow_sample = {"at": pair(now), "mark": _flt(mark_of(inp["legs"], inp["premium"])),
                                            "underlying_last": inp["underlying"]["last"], "quotes": q}
                    wake["shadow_sample"] = [t.target_id for t in shadows]
                nxt = now + timedelta(seconds=min(steps) if steps else self.rules["config"]["poll_seconds"])
            else:
                nxt = self._sample_at(d) if shadows and d.isoformat() not in self._sampled else self.cal.close_dt(d)
            wake["alerts"] = len(self.alerts) - n_alerts
            if shadows and d.isoformat() not in self._sampled:
                nxt = min(nxt, max(self._sample_at(d), now + timedelta(seconds=1)))
            nxt = min(nxt, self.cal.close_dt(d))        # always wake at the close (shadow EOD)
        else:
            n_alerts = len(self.alerts)
            if self.cal.is_trading_day(d) and now >= self.cal.close_dt(d):
                for tg in shadows:
                    s = tg.shadow_sample if (tg.shadow_sample and tg.shadow_sample["at"]["ny"][:10] == d.isoformat()) else None
                    inp = build_input(tg, now, (s or {}).get("quotes") or {}, self.events, self.exdivs)
                    inp.update(self._shadow_fields(tg, s))
                    alerts, tg.state, _ = self.engine.poll(inp, tg.state, self.rules, self.cal)
                    self._emit(tg, alerts)
            wake["alerts"] = len(self.alerts) - n_alerts
            wake["gated"] = f"market {why}: no quote request"
            nxt = next_open(self.cal, now)
        self.wakes.append(wake)
        return nxt

    def _shadow_fields(self, tg: Target, s: Optional[dict]) -> dict:
        ml = mark_levels(tg.ticket)
        why = "VETOED" if tg.vetoed else tg.status
        if not s or s["mark"] is None:
            return {"shadow_value": None, "shadow_threshold": {k: ml[k] for k in ("entry", "stop", "target")},
                    "shadow_detail": f"{why} ticket, shadow EOD: no in-session mark sampled today (UNKNOWN); not watched intraday"}
        from decimal import Decimal
        m = Decimal(str(s["mark"]))
        rel = []
        if ml["stop"] is not None and m <= Decimal(ml["stop"]):
            rel.append(f"at/below would-be stop {ml['stop']}")
        if ml["target"] is not None and m >= Decimal(ml["target"]):
            rel.append(f"at/above would-be target {ml['target']}")
        pnl = (m - Decimal(ml["entry"])) * 100 * int(tg.ticket.get("quantity", 1))
        return {"shadow_value": float(m), "shadow_threshold": {k: ml[k] for k in ("entry", "stop", "target")},
                "shadow_detail": (f"{why} ticket, shadow EOD: would-be mark {m} at {s['at']['ny'][11:16]} NY vs entry {ml['entry']}"
                                  f" (hypothetical {'+' if pnl >= 0 else ''}{pnl:.0f} USD); "
                                  + ("; ".join(rel) if rel else "between would-be stop and target")
                                  + "; not watched intraday")}

    # -- run
    def run(self, clock, until: datetime, once: bool = False, max_wakes: int = 100000) -> dict:
        start = clock.now()
        for _ in range(max_wakes):
            now = clock.now()
            if now > until:
                break
            nxt = self.step(now)
            if once or nxt is None or nxt > until:
                break
            clock.sleep_until(nxt)
        return self.document(start, until, clock)

    def _market_days(self) -> list[dict]:
        days = sorted({datetime.fromisoformat(w["at"]["ny"]).date() for w in self.wakes})
        return [{"date": d.isoformat(), "trading_day": self.cal.is_trading_day(d),
                 "open": pair(open_dt(self.cal, d)) if self.cal.is_trading_day(d) else None,
                 "close": pair(self.cal.close_dt(d)) if self.cal.is_trading_day(d) else None} for d in days]

    def document(self, start: datetime, until: datetime, clock) -> dict:
        sha = None
        if self.session_path and Path(self.session_path).exists():
            sha = hashlib.sha256(Path(self.session_path).read_bytes()).hexdigest()
        tag = next((n for n in (self.rec.get("session") or {}).get("notes") or []
                    if isinstance(n, str) and n.startswith("FICTIONAL DEMO")), None)
        mode = "replay" if isinstance(self.source, ReplayTape) else self.source.name
        return {
            "label": LABEL,
            "watch_version": WATCH_VERSION,
            "watcher_rules_version": self.rules.get("watcher_version"),
            "engine": self.engine.name,
            "scenario_note": tag,
            "session": {"id": (self.rec.get("session") or {}).get("id"), "path": _rel(self.session_path) if self.session_path else None,
                        "sha256": sha, "schema_version": self.rec.get("schema_version")},
            "generated_at": pair(datetime.now(NY)) if isinstance(clock, RealClock) else pair(clock.now()),
            "mode": {"clock": "simulated" if isinstance(clock, SimClock) else "real", "quotes": mode,
                     "quote_method": self.source.method,
                     "tape": _rel(getattr(self.source, "path", None)) if getattr(self.source, "path", None) else None,
                     "tape_label": getattr(self.source, "label", None)},
            "window": {"start": pair(start), "until": pair(until)},
            "market_days": self._market_days(),
            "targets": [t.header() for t in self.targets],
            "summary": {"targets": len(self.targets), "watched": sum(not t.shadow for t in self.targets),
                        "shadow": sum(t.shadow for t in self.targets), "vetoed": sum(t.vetoed for t in self.targets),
                        "hypothetical": sum(t.hypothetical for t in self.targets), "wakes": len(self.wakes),
                        "quote_calls": len(self.source.calls), "alerts": len(self.alerts),
                        "by_level": {lv: sum(a["level"] == lv for a in self.alerts) for lv in ("info", "warn", "exit_signal")}},
            "wakes": self.wakes,
            "alerts": self.alerts,
            "final_state": {t.target_id: t.state for t in self.targets},
            "notes": self.notes,
        }


# ------------------------------------------------------------------ files
def watch_paths(session_json: Path, out_dir: Optional[Path] = None) -> tuple[Path, Path]:
    out = Path(out_dir) if out_dir else session_json.parent
    stem = session_json.name[:-5] if session_json.name.endswith(".json") else session_json.name
    j = out / f"{stem}.watch.json"
    return j, out / f"{j.name}.sha256"


def write_watch(doc: dict, session_json: Path, out_dir: Optional[Path] = None) -> WatchResult:
    j, s = watch_paths(Path(session_json), out_dir)
    j.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n").encode()
    j.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    s.write_text(f"{sha}  {j.name}\n")
    return WatchResult(doc, j, sha)


def verify_sidecar(json_path: Path) -> bool:
    side = Path(str(json_path) + ".sha256")
    want, name = side.read_text().split()
    return name == Path(json_path).name and hashlib.sha256(Path(json_path).read_bytes()).hexdigest() == want


WATCH_SCHEMA = {
    "type": "object",
    "required": ["label", "watch_version", "session", "generated_at", "mode", "window", "targets", "summary",
                 "wakes", "alerts", "final_state", "notes"],
    "properties": {
        "label": {"type": "string", "pattern": "^PAPER ONLY"},
        "mode": {"type": "object", "required": ["clock", "quotes", "quote_method"],
                 "properties": {"quote_method": {"const": "GET"}}},
        "targets": {"type": "array", "items": {"type": "object",
                    "required": ["target_id", "kind", "ticket_id", "status", "vetoed", "hypothetical", "watched_intraday"]}},
        "alerts": {"type": "array", "items": {"type": "object",
                   "required": ["rule_id", "level", "target_id", "ticket_id", "at", "detail", "message", "vetoed", "hypothetical"],
                   "properties": {"level": {"enum": ["info", "warn", "exit_signal"]},
                                  "message": {"type": "string", "pattern": "alert only, no order$"}}}},
        "wakes": {"type": "array", "items": {"type": "object", "required": ["at", "market", "quote_calls"]}},
    },
}


def validate_watch(doc: dict) -> list[str]:
    """Schema + invariants: shadow targets never alert intraday, no quote call while closed, every
    vetoed ticket is flagged."""
    import jsonschema
    errs = [f"{'/'.join(map(str, e.path))}: {e.message}" for e in jsonschema.Draft202012Validator(WATCH_SCHEMA).iter_errors(doc)]
    shadow = {t["target_id"] for t in doc.get("targets", []) if not t["watched_intraday"]}
    for a in doc.get("alerts", []):
        if a["target_id"] in shadow and a["rule_id"] != spec_engine.SHADOW_RULE:
            errs.append(f"shadow target {a['target_id']} got intraday alert {a['rule_id']}")
        if a["vetoed"] and a["target_status"] != "vetoed":
            errs.append(f"{a['target_id']} vetoed flag mismatch")
    for w in doc.get("wakes", []):
        if w["market"] != "open" and w["quote_calls"]:
            errs.append(f"quote call while market {w['market']} at {w['at']['ny']}")
    for t in doc.get("targets", []):
        if t["status"] == "vetoed" and not t["vetoed"]:
            errs.append(f"{t['target_id']} vetoed but not flagged")
    return errs
