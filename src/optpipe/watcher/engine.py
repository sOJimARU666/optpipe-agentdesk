"""Watcher rule engine: a pure port of Doodle's spec/watcher_reference.py (watcher_rules.yaml v1.1.0).

poll(inp, state, rules, cal) -> (alerts_to_send, new_state, next_poll_seconds | None)

ALERT ONLY: nothing here creates, changes or cancels an order, and nothing touches the network. Every
alert message ends with the configured alert_text ("alert only, no order"). Parity with the reference on
every YAML case is tested in tests/test_watcher.py. Owner: Doodle (rules) / Poodle (code).
"""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from ..risk.engine import Calendar

NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")
LEVELS = {"info": 0, "warn": 1, "exit_signal": 2}
WATCHED = {("position", "open"), ("ticket", "approved_pending")}
SHADOW = {"vetoed", "rejected"}
SHADOW_RULE = "SHADOW_EOD"
Alert = dict[str, Any]


def D(x) -> Decimal:
    return Decimal(str(x))


def _ts(s: str | datetime) -> datetime:
    dt = s if isinstance(s, datetime) else datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp forbidden: {s}")
    return dt


def _usable(f) -> bool:
    return isinstance(f, dict) and f.get("status") in ("ok", "stale") and f.get("value") is not None


def _pair(dt: datetime) -> dict:
    return {"ny": dt.astimezone(NY).isoformat(), "berlin": dt.astimezone(BERLIN).isoformat()}


def mid(leg: dict) -> Optional[Decimal]:
    if not (_usable(leg.get("bid")) and _usable(leg.get("ask"))):
        return None
    b, a = D(leg["bid"]["value"]), D(leg["ask"]["value"])
    if b < 0 or a <= 0 or a < b:
        return None
    return (b + a) / 2


def _sign(leg: dict) -> int:
    return 1 if leg["side"] == "buy" else -1


def round_tick(x, tick) -> Decimal:
    t = D(tick)
    return (D(x) / t).quantize(Decimal(1), rounding=ROUND_HALF_UP) * t


def mark_of(legs: list[dict], premium: str) -> Optional[Decimal]:
    """Per-share mark at mid: debit = value to sell; credit = cost to buy back (negated sum)."""
    mids = [mid(l) for l in legs]
    if any(m is None for m in mids):
        return None
    raw = sum((_sign(l) * D(l["ratio"]) * m for l, m in zip(legs, mids)), D(0))
    return raw if premium == "long" else -raw


class _Poll:
    def __init__(self, inp: dict, cfg: dict, cal: Calendar):
        self.inp, self.cfg, self.cal = inp, cfg, cal
        self.t = _ts(inp["now"])
        self.n = self.t.astimezone(NY)
        self.today = self.n.date()
        self.legs = inp["legs"]
        last = inp["underlying"]["last"]
        self.S = D(last["value"]) if _usable(last) else None
        self.near = False

    def quote_fields(self):
        yield self.inp["underlying"]["last"]
        for l in self.legs:
            yield l["bid"]
            yield l["ask"]

    def oldest_as_of(self, fields=None) -> Optional[str]:
        xs = [f["as_of"] for f in (fields or self.quote_fields()) if f and f.get("as_of")]   # as reference: [] -> all quotes
        return min(xs, key=_ts) if xs else None

    def dte(self) -> int:
        return min((date.fromisoformat(l["expiry"]) - self.today).days for l in self.legs)

    def at_or_after(self, d: date, hhmm: str) -> bool:
        return self.n >= datetime.combine(d, time.fromisoformat(hhmm), NY)

    def alert(self, rule: str, level: str, value, threshold, detail: str, fields=None) -> Alert:
        return {"rule_id": rule, "level": level, "target_id": self.inp["target"]["id"], "value": value,
                "threshold": threshold, "quote_as_of": self.oldest_as_of(fields), "at": _pair(self.t), "detail": detail}


# ------------------------------------------------------------------ W01..W07
def w01(w: _Poll) -> Optional[Alert]:
    mark = mark_of(w.legs, w.inp["premium"])
    if mark is None:
        return None          # W06 reports the data problem
    ml = w.inp["mark_levels"]
    tick = ml.get("tick_size") or w.cfg["default_tick_size"]
    entry, band = D(ml["entry"]), D(w.cfg["near_level_pct"])
    best = None
    for name in ("stop", "target"):
        if ml.get(name) is None:
            continue
        lvl = round_tick(ml[name], tick)
        beyond = (mark - lvl) * (lvl - entry) >= 0
        near = abs(mark - lvl) <= band * abs(entry - lvl)
        lev = "exit_signal" if beyond else ("warn" if near else None)
        if lev and (best is None or LEVELS[lev] > LEVELS[best[0]]):
            best = (lev, name, lvl)
    if best is None:
        return None
    w.near = True
    lev, name, lvl = best
    return w.alert("W01_MARK_VS_STOP_TARGET", lev, float(mark), {name: float(lvl), "near_level_pct": w.cfg["near_level_pct"]},
                   f"mark {mark} {'at/beyond' if lev == 'exit_signal' else 'within 10% of'} {name} {lvl}",
                   [f for l in w.legs for f in (l["bid"], l["ask"])])


def w02(w: _Poll) -> Optional[Alert]:
    u = w.inp["underlying"]
    if w.S is None or u.get("prev_last") is None:
        return None
    prev, last = D(u["prev_last"]), w.S
    lv = u.get("levels") or {}
    out = []
    bars = [b for b in u.get("bars_5m") or [] if _ts(b["end"]) <= w.t]
    bar = max(bars, key=lambda b: _ts(b["end"])) if bars else None
    below, above = lv.get("stop_below"), lv.get("stop_above")
    if below is not None:
        if prev > D(below) >= last:
            out.append(("warn", f"crossed below stop {below}", below))
        if bar and D(bar["close"]) < D(below):
            out.append(("exit_signal", f"5-min close {bar['close']} below stop {below}", below))
    if above is not None:
        if prev < D(above) <= last:
            out.append(("warn", f"crossed above stop {above}", above))
        if bar and D(bar["close"]) > D(above):
            out.append(("exit_signal", f"5-min close {bar['close']} above stop {above}", above))
    for be in lv.get("breakevens") or []:
        if prev != last and min(prev, last) <= D(be) <= max(prev, last):
            out.append(("info", f"crossed breakeven {be}", be))
    if not out:
        return None
    lev, why, lvl = max(out, key=lambda x: LEVELS[x[0]])
    return w.alert("W02_UNDERLYING_LEVELS", lev, float(last), {"level": lvl}, why, [u["last"]])


def w03(w: _Poll) -> Optional[Alert]:
    if any(l.get("entry_iv") is None for l in w.legs) or not all(_usable(l.get("iv")) for l in w.legs):
        return w.alert("W03_IV_VS_ENTRY", "info", None, {"entry_iv": None},
                       "UNKNOWN: entry IV or current IV not available", [l.get("iv") for l in w.legs if l.get("iv")])
    ch = [(D(l["iv"]["value"]) / D(l["entry_iv"]) - 1, l) for l in w.legs]
    c, leg = max(ch, key=lambda x: abs(x[0]))
    thr = {"warn_abs": w.cfg["iv_warn_change"], "exit_long_premium": w.cfg["iv_exit_change_long_premium"]}
    lev = None
    if w.inp["premium"] == "long" and min(x[0] for x in ch) <= D(w.cfg["iv_exit_change_long_premium"]):
        c, leg = min(ch, key=lambda x: x[0])
        lev = "exit_signal"
    elif abs(c) >= D(w.cfg["iv_warn_change"]):
        lev = "warn"
    if lev is None:
        return None
    return w.alert("W03_IV_VS_ENTRY", lev, float(c), thr,
                   f"{leg['leg_id']} IV {leg['iv']['value']} vs entry {leg['entry_iv']} ({c:+.1%})", [leg["iv"]])


def w04(w: _Poll) -> Optional[Alert]:
    dte = w.dte()
    stop = w.inp.get("time_stop")
    expiry = min(date.fromisoformat(l["expiry"]) for l in w.legs)
    ts_dte = (expiry - _ts(stop).astimezone(NY).date()).days if stop else 0
    thr = {"warn_dte": ts_dte + w.cfg["time_stop_warn_extra_days"], "time_stop": stop}
    if stop:
        sd = _ts(stop).astimezone(NY).date()
        if w.at_or_after(sd, w.cfg["time_stop_exit_at"]):
            return w.alert("W04_TIME", "exit_signal", dte, thr, f"time stop {sd} reached (>= {w.cfg['time_stop_exit_at']} NY)", [])
    if any(date.fromisoformat(l["expiry"]) == w.today for l in w.legs) and w.n.time() >= time.fromisoformat(w.cfg["expiry_exit_at"]):
        return w.alert("W04_TIME", "exit_signal", dte, thr, f"expiry day, >= {w.cfg['expiry_exit_at']} NY with open legs", [])
    if dte <= thr["warn_dte"]:
        return w.alert("W04_TIME", "warn", dte, thr,
                       f"DTE {dte} <= time-stop DTE {ts_dte} + {w.cfg['time_stop_warn_extra_days']}", [])
    return None


def w05(w: _Poll) -> Optional[Alert]:
    if w.S is None:
        return None
    out = []
    exd = w.inp.get("ex_dividend")
    for l in w.legs:
        if l["side"] != "sell":
            continue
        k = D(l["strike"])
        intr = max(D(0), w.S - k) if l["type"] == "call" else max(D(0), k - w.S)
        if intr <= 0:
            continue
        m = mid(l)
        dte = (date.fromisoformat(l["expiry"]) - w.today).days
        if m is not None:
            ext = m - intr
            if ext < D(w.cfg["assignment_min_extrinsic"]):
                out.append(("exit_signal", float(ext),
                            f"{l['leg_id']} short {l['type']} ITM, extrinsic {ext} < {w.cfg['assignment_min_extrinsic']}", l))
            if (exd and l["type"] == "call" and ext < D(exd["amount"])
                    and w.cal.prev_trading_day(date.fromisoformat(exd["date"])) == w.today):
                out.append(("exit_signal", float(ext), f"{l['leg_id']} short call ITM day before ex-div {exd['date']}, "
                                                       f"extrinsic {ext} < dividend {exd['amount']}", l))
        if dte <= w.cfg["assignment_warn_dte"]:
            out.append(("warn", float(intr), f"{l['leg_id']} short {l['type']} ITM by {intr} with DTE {dte}", l))
    if not out:
        return None
    lev, val, why, l = max(out, key=lambda x: LEVELS[x[0]])
    return w.alert("W05_SHORT_LEG_ASSIGNMENT", lev, val,
                   {"warn_dte": w.cfg["assignment_warn_dte"], "min_extrinsic": w.cfg["assignment_min_extrinsic"]},
                   why, [w.inp["underlying"]["last"], l["bid"], l["ask"]])


def w06(w: _Poll, state: dict) -> tuple[Optional[Alert], bool, dict]:
    """Data health -> (alert | None, any quote stale, new wide_streak)."""
    max_age = w.cfg["quote_max_age_seconds"]
    stale = [f for f in w.quote_fields()
             if not f or f.get("status") != "ok" or f.get("as_of") is None
             or (w.t - _ts(f["as_of"])).total_seconds() > max_age]
    streak = dict(state.get("wide_streak", {}))
    wide = []
    for l in w.legs:
        m = mid(l)
        is_wide = m is None or m <= 0 or (D(l["ask"]["value"]) - D(l["bid"]["value"])) / m > D(w.cfg["max_spread_pct_of_mid"])
        streak[l["leg_id"]] = streak.get(l["leg_id"], 0) + 1 if is_wide else 0
        if streak[l["leg_id"]] >= w.cfg["wide_spread_polls"]:
            wide.append(l["leg_id"])
    if not stale and not wide:
        return None, False, streak
    why = []
    if stale:
        why.append(f"{len(stale)} quote(s) stale/older than {max_age // 60} min; exit signals suppressed")
    if wide:
        why.append(f"spread > {w.cfg['max_spread_pct_of_mid']} of mid for {w.cfg['wide_spread_polls']}+ polls: {', '.join(wide)}")
    a = w.alert("W06_DATA_HEALTH", "warn", len(stale) + len(wide),
                {"quote_max_age_seconds": max_age, "max_spread_pct_of_mid": w.cfg["max_spread_pct_of_mid"],
                 "wide_spread_polls": w.cfg["wide_spread_polls"]}, "; ".join(why))
    return a, bool(stale), streak


def w07(w: _Poll) -> Optional[Alert]:
    hw = w.inp["hold_window"]
    a, b = _ts(hw["start"]), _ts(hw["end"])
    sym = w.inp["underlying"]["symbol"]
    hits = []
    for ev in w.inp.get("events") or []:
        if ev["kind"] not in w.cfg["event_kinds"] or (ev["kind"] == "earnings" and ev.get("underlying") != sym):
            continue
        at = _ts(ev["at"])
        if a <= at <= b and w.cal.prev_trading_day(at.astimezone(NY).date()) == w.today:
            hits.append(ev)
    if not hits:
        return None
    return w.alert("W07_EVENT_EVE", "info", len(hits), {"event_kinds": w.cfg["event_kinds"]},
                   "next session: " + ", ".join(f"{e['kind']} {e['event_id']} at {_ts(e['at']).astimezone(NY).isoformat()}"
                                                for e in hits), [])


QUOTE_RULES: dict[str, Callable[[_Poll], Optional[Alert]]] = {
    "W01_MARK_VS_STOP_TARGET": w01, "W02_UNDERLYING_LEVELS": w02, "W03_IV_VS_ENTRY": w03,
    "W04_TIME": w04, "W05_SHORT_LEG_ASSIGNMENT": w05}
SPECIAL_RULES = {"W06_DATA_HEALTH", "W07_EVENT_EVE"}


def message(a: Alert, cfg: dict) -> str:
    return (f"[{a['level']}] {a['rule_id']} {a['target_id']}: {a['detail']} | value={a['value']} threshold={a['threshold']} "
            f"quote_as_of={a['quote_as_of']} | {cfg['alert_text']}")


def gate(alerts: list[Alert], state: dict, now: datetime, cfg: dict):
    """Dedupe on (rule, target, level), escalation only, cooldown per (rule, target) unless exit_signal.
    Held-back alerts leave the state untouched."""
    sent, last_at = dict(state.get("sent", {})), dict(state.get("last_at", {}))
    out = []
    for a in alerts:
        key = f"{a['rule_id']}|{a['target_id']}"
        prev = sent.get(key)
        if prev is not None and LEVELS[a["level"]] <= LEVELS[prev]:
            continue
        if (key in last_at and a["level"] != "exit_signal"
                and now - _ts(last_at[key]) < timedelta(minutes=cfg["cooldown_minutes"])):
            continue
        sent[key], last_at[key] = a["level"], now.isoformat()
        out.append(a)
    return out, sent, last_at


def check_rules(rules: dict) -> None:
    """Fail loudly if Doodle adds a rule this engine does not implement."""
    ids = {r["id"] for r in rules["rules"]}
    missing = ids - set(QUOTE_RULES) - SPECIAL_RULES
    if missing:
        raise NotImplementedError(f"watcher rules without an implementation: {sorted(missing)}")


def poll(inp: dict, state: Optional[dict], rules: dict, cal: Calendar) -> tuple[list[Alert], dict, Optional[int]]:
    """Pure: same input + state + rules -> same output; never mutates its arguments."""
    check_rules(rules)
    cfg = rules["config"]
    state = copy.deepcopy(state or {})
    t = _ts(inp["now"])
    n = t.astimezone(NY)
    tgt = inp["target"]
    if tgt["status"] in SHADOW:
        key = f"{SHADOW_RULE}|{tgt['id']}|{n.date()}"
        if cal.is_trading_day(n.date()) and n >= cal.close_dt(n.date()) and key not in state.get("sent", {}):
            w = _Poll(inp, cfg, cal)
            a = w.alert(SHADOW_RULE, "info", inp.get("shadow_value"), inp.get("shadow_threshold"),
                        inp.get("shadow_detail") or f"shadow end-of-day summary for {tgt['status']} ticket (not watched intraday)")
            a["message"] = message(a, cfg)
            state.setdefault("sent", {})[key] = "info"
            return [a], state, None
        return [], state, None
    if (tgt["kind"], tgt["status"]) not in WATCHED or not cal.is_open(t):
        return [], state, None
    w = _Poll(inp, cfg, cal)
    enabled = {r["id"] for r in rules["rules"]}
    cands = [a for rid, f in QUOTE_RULES.items() if rid in enabled and (a := f(w))]
    health, stale, streak = w06(w, state)
    state["wide_streak"] = streak
    if stale:                                   # W06: no exit_signal from stale data (not sent, not recorded)
        cands = [a for a in cands if a["level"] != "exit_signal"]
    if health and "W06_DATA_HEALTH" in enabled:
        cands.append(health)
    if "W07_EVENT_EVE" in enabled and (e := w07(w)):
        cands.append(e)
    sent, state["sent"], state["last_at"] = gate(sorted(cands, key=lambda a: a["rule_id"]), state, t, cfg)
    for a in sent:
        a["message"] = message(a, cfg)
    last_hour = any(date.fromisoformat(l["expiry"]) == n.date()
                    and n >= cal.close_dt(n.date()) - timedelta(minutes=cfg["fast_poll_last_hour_minutes"])
                    for l in w.legs)
    return sent, state, cfg["fast_poll_seconds"] if (w.near or last_hour) else cfg["poll_seconds"]
