"""Reference evaluator for watcher_rules.yaml (alert-only Watcher, spec v1.1; target_from_session added in spec 1.1.3).

Purpose: prove the watcher rules are unambiguous. Not production code.
poll(inp, state) is a pure function: it returns the alerts to SEND for this poll, the new
dedupe/cooldown/streak state, and the seconds until the next poll (None = market closed).
It never creates, changes or cancels an order: every alert message ends with "alert only, no order".
Approval state (target_from_session / real_approval) is read ONLY from approvals[].status; there is no
'decision' field (see watcher_rules.md "Approval state").
"""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

import risk_reference as rr
from risk_reference import NY, D, ts, usable

SPEC_DIR = Path(__file__).resolve().parent
LEVELS = {"info": 0, "warn": 1, "exit_signal": 2}
WATCHED = {("position", "open"), ("ticket", "approved_pending")}
SHADOW = {"vetoed", "rejected"}


def load_watcher_rules(path: Path = SPEC_DIR / "watcher_rules.yaml") -> dict:
    return yaml.safe_load(path.read_text())


def calendar() -> rr.Calendar:
    return rr.Calendar(rr.load_rules()["config"]["market_hours"])


def _mid(leg):
    if not (usable(leg.get("bid")) and usable(leg.get("ask"))):
        return None
    b, a = D(leg["bid"]["value"]), D(leg["ask"]["value"])
    if b < 0 or a <= 0 or a < b:
        return None
    return (b + a) / 2


def _sign(l):
    return 1 if l["side"] == "buy" else -1


def _round_tick(x, tick):
    tick = D(tick)
    return (D(x) / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick


class W:
    def __init__(self, inp, cfg, cal):
        self.inp, self.cfg, self.cal = inp, cfg, cal
        self.t = ts(inp["now"])
        self.n = self.t.astimezone(NY)
        self.today = self.n.date()
        self.legs = inp["legs"]
        self.S = D(inp["underlying"]["last"]["value"]) if usable(inp["underlying"]["last"]) else None
        self.near = False  # set by W01 for the fast-poll decision

    def quote_fields(self):
        yield self.inp["underlying"]["last"]
        for l in self.legs:
            yield l["bid"]; yield l["ask"]

    def oldest_as_of(self, fields=None):
        xs = [f["as_of"] for f in (fields or self.quote_fields()) if f and f.get("as_of")]
        return min(xs, key=lambda s: ts(s)) if xs else None

    def dte(self):
        return min((date.fromisoformat(l["expiry"]) - self.today).days for l in self.legs)

    def alert(self, rule, level, value, threshold, detail, fields=None):
        return {"rule_id": rule, "level": level, "target_id": self.inp["target"]["id"], "value": value,
                "threshold": threshold, "quote_as_of": self.oldest_as_of(fields), "at": rr.tspair(self.t), "detail": detail}


# ------------------------------------------------------------------ rules (each -> alert | None)
def w01(w: W):
    mids = [_mid(l) for l in w.legs]
    if any(m is None for m in mids):
        return None
    raw = sum(_sign(l) * D(l["ratio"]) * m for l, m in zip(w.legs, mids))
    mark = raw if w.inp["premium"] == "long" else -raw
    ml = w.inp["mark_levels"]
    tick = ml.get("tick_size") or w.cfg["default_tick_size"]
    entry = D(ml["entry"])
    band = D(w.cfg["near_level_pct"])
    best = None
    for name in ("stop", "target"):
        if ml.get(name) is None:
            continue
        lvl = _round_tick(ml[name], tick)
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


def w02(w: W):
    u = w.inp["underlying"]
    if w.S is None or u.get("prev_last") is None:
        return None
    prev, last = D(u["prev_last"]), w.S
    lv = u.get("levels") or {}
    out = []
    below, above = lv.get("stop_below"), lv.get("stop_above")
    bars = [b for b in u.get("bars_5m") or [] if ts(b["end"]) <= w.t]
    bar = max(bars, key=lambda b: ts(b["end"])) if bars else None
    if below is not None:
        if prev > D(below) >= last: out.append(("warn", f"crossed below stop {below}", below))
        if bar and D(bar["close"]) < D(below): out.append(("exit_signal", f"5-min close {bar['close']} below stop {below}", below))
    if above is not None:
        if prev < D(above) <= last: out.append(("warn", f"crossed above stop {above}", above))
        if bar and D(bar["close"]) > D(above): out.append(("exit_signal", f"5-min close {bar['close']} above stop {above}", above))
    for be in lv.get("breakevens") or []:
        if min(prev, last) <= D(be) <= max(prev, last) and prev != last:
            out.append(("info", f"crossed breakeven {be}", be))
    if not out:
        return None
    lev, why, lvl = max(out, key=lambda x: LEVELS[x[0]])
    return w.alert("W02_UNDERLYING_LEVELS", lev, float(last), {"level": lvl}, why, [u["last"]])


def w03(w: W):
    if any(l.get("entry_iv") is None for l in w.legs) or not all(usable(l.get("iv")) for l in w.legs):
        return w.alert("W03_IV_VS_ENTRY", "info", None, {"entry_iv": None}, "UNKNOWN: entry IV or current IV not available",
                       [l.get("iv") for l in w.legs if l.get("iv")])
    ch = [(D(l["iv"]["value"]) / D(l["entry_iv"]) - 1, l) for l in w.legs]
    c, leg = max(ch, key=lambda x: abs(x[0]))
    thr = {"warn_abs": w.cfg["iv_warn_change"], "exit_long_premium": w.cfg["iv_exit_change_long_premium"]}
    lev = None
    if w.inp["premium"] == "long" and min(x[0] for x in ch) <= D(w.cfg["iv_exit_change_long_premium"]):
        c, leg = min(ch, key=lambda x: x[0]); lev = "exit_signal"
    elif abs(c) >= D(w.cfg["iv_warn_change"]):
        lev = "warn"
    if lev is None:
        return None
    return w.alert("W03_IV_VS_ENTRY", lev, float(c), thr, f"{leg['leg_id']} IV {leg['iv']['value']} vs entry {leg['entry_iv']} ({c:+.1%})", [leg["iv"]])


def w04(w: W):
    dte = w.dte()
    stop = w.inp.get("time_stop")
    expiry = min(date.fromisoformat(l["expiry"]) for l in w.legs)
    ts_dte = (expiry - ts(stop).astimezone(NY).date()).days if stop else 0
    thr = {"warn_dte": ts_dte + w.cfg["time_stop_warn_extra_days"], "time_stop": stop}
    if stop:
        sd = ts(stop).astimezone(NY).date()
        if self_at(w, sd, w.cfg["time_stop_exit_at"]):
            return w.alert("W04_TIME", "exit_signal", dte, thr, f"time stop {sd} reached (>= {w.cfg['time_stop_exit_at']} NY)", [])
    if any(date.fromisoformat(l["expiry"]) == w.today for l in w.legs) and w.n.time() >= time.fromisoformat(w.cfg["expiry_exit_at"]):
        return w.alert("W04_TIME", "exit_signal", dte, thr, f"expiry day, >= {w.cfg['expiry_exit_at']} NY with open legs", [])
    if dte <= thr["warn_dte"]:
        return w.alert("W04_TIME", "warn", dte, thr, f"DTE {dte} <= time-stop DTE {ts_dte} + {w.cfg['time_stop_warn_extra_days']}", [])
    return None


def self_at(w: W, d: date, hhmm: str) -> bool:
    """now is at/after hh:mm NY on day d (or any later day)."""
    return w.n >= datetime.combine(d, time.fromisoformat(hhmm), NY)


def w05(w: W):
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
        m = _mid(l)
        dte = (date.fromisoformat(l["expiry"]) - w.today).days
        if m is not None:
            ext = m - intr
            if ext < D(w.cfg["assignment_min_extrinsic"]):
                out.append(("exit_signal", float(ext), f"{l['leg_id']} short {l['type']} ITM, extrinsic {ext} < {w.cfg['assignment_min_extrinsic']}", l))
            if exd and l["type"] == "call" and w.cal.prev_trading_day(date.fromisoformat(exd["date"])) == w.today and ext < D(exd["amount"]):
                out.append(("exit_signal", float(ext), f"{l['leg_id']} short call ITM day before ex-div {exd['date']}, extrinsic {ext} < dividend {exd['amount']}", l))
        if dte <= w.cfg["assignment_warn_dte"]:
            out.append(("warn", float(intr), f"{l['leg_id']} short {l['type']} ITM by {intr} with DTE {dte}", l))
    if not out:
        return None
    lev, val, why, l = max(out, key=lambda x: LEVELS[x[0]])
    return w.alert("W05_SHORT_LEG_ASSIGNMENT", lev, val, {"warn_dte": w.cfg["assignment_warn_dte"], "min_extrinsic": w.cfg["assignment_min_extrinsic"]},
                   why, [w.inp["underlying"]["last"], l["bid"], l["ask"]])


def data_health(w: W, state):
    """-> (alert | None, stale: bool, new wide_streak)"""
    max_age = w.cfg["quote_max_age_seconds"]
    stale = []
    for f in w.quote_fields():
        if not f or f.get("status") != "ok" or f.get("as_of") is None or (w.t - ts(f["as_of"])).total_seconds() > max_age:
            stale.append(f)
    streak = dict(state.get("wide_streak", {}))
    wide = []
    for l in w.legs:
        m = _mid(l)
        is_wide = m is None or m <= 0 or (D(l["ask"]["value"]) - D(l["bid"]["value"])) / m > D(w.cfg["max_spread_pct_of_mid"])
        streak[l["leg_id"]] = streak.get(l["leg_id"], 0) + 1 if is_wide else 0
        if streak[l["leg_id"]] >= w.cfg["wide_spread_polls"]:
            wide.append(l["leg_id"])
    if not stale and not wide:
        return None, False, streak
    why = []
    if stale: why.append(f"{len(stale)} quote(s) stale/older than {max_age // 60} min; exit signals suppressed")
    if wide: why.append(f"spread > {w.cfg['max_spread_pct_of_mid']} of mid for {w.cfg['wide_spread_polls']}+ polls: {', '.join(wide)}")
    a = w.alert("W06_DATA_HEALTH", "warn", len(stale) + len(wide), {"quote_max_age_seconds": max_age, "max_spread_pct_of_mid": w.cfg["max_spread_pct_of_mid"],
                "wide_spread_polls": w.cfg["wide_spread_polls"]}, "; ".join(why))
    return a, bool(stale), streak


def w07(w: W):
    hw = w.inp["hold_window"]
    a, b = ts(hw["start"]), ts(hw["end"])
    sym = w.inp["underlying"]["symbol"]
    hits = []
    for ev in w.inp.get("events") or []:
        if ev["kind"] not in w.cfg["event_kinds"] or (ev["kind"] == "earnings" and ev.get("underlying") != sym):
            continue
        at = ts(ev["at"])
        if a <= at <= b and w.cal.prev_trading_day(at.astimezone(NY).date()) == w.today:
            hits.append(ev)
    if not hits:
        return None
    return w.alert("W07_EVENT_EVE", "info", len(hits), {"event_kinds": w.cfg["event_kinds"]},
                   "next session: " + ", ".join(f"{e['kind']} {e['event_id']} at {ts(e['at']).astimezone(NY).isoformat()}" for e in hits), [])


RULES = [w01, w02, w03, w04, w05]


def _message(a, cfg):
    return (f"[{a['level']}] {a['rule_id']} {a['target_id']}: {a['detail']} | value={a['value']} threshold={a['threshold']} "
            f"quote_as_of={a['quote_as_of']} | {cfg['alert_text']}")


def _gate(alerts, state, now, cfg):
    """dedupe on (rule, target, level); escalation only; 30-min cooldown per (rule, target) unless exit_signal."""
    sent, last_at = dict(state.get("sent", {})), dict(state.get("last_at", {}))
    out = []
    for a in alerts:
        key = f"{a['rule_id']}|{a['target_id']}"
        prev = sent.get(key)
        if prev is not None and LEVELS[a["level"]] <= LEVELS[prev]:
            continue  # duplicate or de-escalation
        if key in last_at and a["level"] != "exit_signal" and now - ts(last_at[key]) < timedelta(minutes=cfg["cooldown_minutes"]):
            continue  # cooldown hold-back
        sent[key], last_at[key] = a["level"], now.isoformat()
        out.append(a)
    return out, sent, last_at


def poll(inp: dict, state: dict | None = None, wr: dict | None = None, cal: rr.Calendar | None = None):
    """-> (alerts_to_send, new_state, next_poll_seconds | None)"""
    wr = wr or load_watcher_rules(); cfg = wr["config"]; cal = cal or calendar()
    state = copy.deepcopy(state or {})
    t = ts(inp["now"]); n = t.astimezone(NY)
    tgt = inp["target"]
    alerts = []
    if tgt["status"] in SHADOW:
        key = f"SHADOW_EOD|{tgt['id']}|{n.date()}"
        if cal.is_trading_day(n.date()) and n >= cal.close_dt(n.date()) and key not in state.get("sent", {}):
            w = W(inp, cfg, cal)
            a = w.alert("SHADOW_EOD", "info", None, None, f"shadow end-of-day summary for {tgt['status']} ticket (not watched intraday)")
            a["message"] = _message(a, cfg)
            state.setdefault("sent", {})[key] = "info"
            alerts.append(a)
        return alerts, state, None
    if (tgt["kind"], tgt["status"]) not in WATCHED or not cal.is_open(t):
        return [], state, None
    w = W(inp, cfg, cal)
    cands = [a for a in (f(w) for f in RULES) if a]
    health, stale, streak = data_health(w, state)
    state["wide_streak"] = streak
    if stale:
        cands = [a for a in cands if a["level"] != "exit_signal"]
    if health:
        cands.append(health)
    e = w07(w)
    if e:
        cands.append(e)
    sent_alerts, state["sent"], state["last_at"] = _gate(sorted(cands, key=lambda a: a["rule_id"]), state, t, cfg)
    for a in sent_alerts:
        a["message"] = _message(a, cfg)
    last_hour = any(date.fromisoformat(l["expiry"]) == n.date() and n >= cal.close_dt(n.date()) - timedelta(minutes=cfg["fast_poll_last_hour_minutes"])
                    for l in w.legs)
    nxt = cfg["fast_poll_seconds"] if (w.near or last_hour) else cfg["poll_seconds"]
    return sent_alerts, state, nxt


# ------------------------------------------------- session -> ticket target (spec 1.1.3, FIX-SEC-001)
# APPROVAL STATE IS READ ONLY FROM approvals[].status. There is no 'decision' field in the schema (approval objects have
# additionalProperties: false, so a record carrying 'decision' is schema-invalid); never read, trust or fall back to it.
def real_approval(sess: dict, ticket_id: str, now: str | None = None) -> dict | None:
    """The approval that makes `ticket_id` really approved, or None.

    The canonical approval field is approvals[].status (there is no 'decision' field). An approval counts only if
    status == 'active', it is for this ticket, it is not expired at `now` (approved_at <= now < expires_at; any time
    when now is None), and it passes the 1.1.3 approval checks (validate_session.approval_errors: not a VETO review,
    same ticket, legs/qty/limit equal the ticket, every flag acknowledged). 'expired', 'revoked', 'consumed' or no
    approval -> None."""
    import validate_session as vs  # local import: the validator loads the schema at import time

    tk = next((t for t in sess.get("tickets", []) if t["ticket_id"] == ticket_id), None)
    rvs = {r["review_id"]: r for r in sess.get("risk_reviews", [])}
    t = ts(now) if now else None
    for a in sess.get("approvals", []):
        if a.get("ticket_id") != ticket_id or a.get("status") != "active":
            continue
        if t is not None and not (ts(a["approved_at"]["ny"]) <= t < ts(a["expires_at"]["ny"])):
            continue
        rv = rvs.get(a.get("review_id"))
        if rv is None or tk is None or vs.approval_errors(a, rv, tk):
            continue
        return a
    return None


def target_from_session(sess: dict, ticket_id: str, now: str | None = None) -> dict:
    """Watcher target for a ticket, derived from a session record (scope in watcher_rules.yaml).

    Order matters (VETO first, so an approval can never un-veto a ticket):
      1. the latest risk review of the ticket is VETO (recorded verdict or what its rule results give), or any approval of
         the ticket points at a VETO review, or the idea is 'vetoed'  -> status 'vetoed'   (shadow only)
      2. idea 'rejected'                                               -> status 'rejected' (shadow only)
      3. idea 'approved_pending' / 'approved' / no idea               -> status 'approved_pending' (watched pre-entry);
         approved = a real approval exists (real_approval), hypothetical = not approved
      4. any other idea status (proposed, simulated, executed, closed) -> that status (not watched as a ticket;
         an entered trade is watched as its paper position)."""
    reviews = sorted((r for r in sess.get("risk_reviews", []) if r["ticket_id"] == ticket_id),
                     key=lambda r: ts(r["evaluated_at"]["ny"]))
    rv_by_id = {r["review_id"]: r for r in sess.get("risk_reviews", [])}
    idea = next((i for i in sess.get("idea_log", []) if i.get("ticket_id") == ticket_id), None)
    istatus = idea["current_status"] if idea else None

    import validate_session as vs

    def veto(rv):  # recorded VETO, derived VETO, or no rule results (fail closed)
        return rv is not None and vs.review_is_veto(rv)

    base = {"kind": "ticket", "id": ticket_id, "ticket_id": ticket_id}
    if (reviews and veto(reviews[-1])) or istatus == "vetoed" or any(
            veto(rv_by_id.get(a.get("review_id"))) for a in sess.get("approvals", []) if a.get("ticket_id") == ticket_id):
        return {**base, "status": "vetoed", "approved": False, "hypothetical": True, "approval_id": None}
    if istatus == "rejected":
        return {**base, "status": "rejected", "approved": False, "hypothetical": True, "approval_id": None}
    if istatus in (None, "approved_pending", "approved"):
        a = real_approval(sess, ticket_id, now)
        return {**base, "status": "approved_pending", "approved": a is not None, "hypothetical": a is None,
                "approval_id": a["approval_id"] if a else None}
    return {**base, "status": istatus, "approved": False, "hypothetical": True, "approval_id": None}
