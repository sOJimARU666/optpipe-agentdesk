"""Reference evaluator for watcher_rules.yaml (alert-only Watcher, spec v1.1; target_from_session added in spec 1.1.3;
spec 1.1.4: a revoked approval makes the ticket target 'rejected', see APPROVAL_STATUS_TARGET;
spec 1.1.5: a review failing INV-16 (stored overall != recomputed) is VETO for the watcher; executed / closed ideas keep
their status; a revoked approval rejects every pre-execution ticket; a real approval needs an approved_pending / approved
idea; see TARGET_PRECEDENCE).
spec 1.1.6 (SOL-ACCEPT-001 G3/G4/G5): an approval is real only while approved_at < now < expires_at (open window, same
as INV-06 at filled_at; instants compared whatever the UTC offset); a naive / unparsable `now`, approval time or review
evaluated_at never crashes: it fails closed and the target carries an `errors` list with the INV id; review verdicts use
the fail-closed risk_reference.overall (an unrecognised result string, or UNKNOWN without severity, is VETO).

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


# ------------------------------------------------- session -> ticket target (spec 1.1.3, FIX-SEC-001; 1.1.4/1.1.5 rulings)
# Approval status -> ticket target (spec 1.1.4 ruling; mirrors watcher_rules.yaml scope.approval_status). 'real' = watched
# as approved_pending with approved=True (only if the approval also passes real_approval: inside its window, clean INV-05
# and INV-16); an active approval that fails those checks is hypothetical. 'revoked' is fail closed: Eugene withdrew the
# approval, so the ticket is a rejected shadow, never a watched what-if (spec 1.1.5: for every pre-execution idea status;
# an executed / closed idea keeps its status).
APPROVAL_STATUS_TARGET = {
    "active": "real",               # approved_pending, approved=True (else hypothetical if outside window / INV-05/16 fails)
    "revoked": "rejected",          # spec 1.1.4: shadow only (was hypothetical in 1.1.3); 1.1.5: any pre-execution idea
    "expired": "hypothetical",      # approved_pending, approved=False
    "consumed": "hypothetical",     # approved_pending, approved=False (an entered trade is watched as its paper position)
    "none": "hypothetical",         # missing: no approval for the ticket
}
# Full precedence of target_from_session (spec 1.1.5; mirrors watcher_rules.yaml scope.target_precedence). First match wins.
TARGET_PRECEDENCE = (
    "vetoed",            # 1. latest review blocked (VETO recorded/derived, no results, or INV-16 inconsistent), any approval
                         #    of the ticket on a blocked review, or idea 'vetoed'
    "terminal",          # 2. idea 'executed' / 'closed' keeps its status (a revoked approval does not relabel it; the
                         #    trade is watched by the position rules)
    "rejected",          # 3. pre-execution idea (any other status, or no idea): idea 'rejected', or any 'revoked' approval
                         #    of the ticket and no real approval
    "approved_pending",  # 4a. idea approved_pending / approved / none: real approval -> approved; else hypothetical
    "idea_status",       # 4b. any other non-approved idea status (proposed, simulated, ...) -> passed through, not watched
)
TERMINAL_IDEA_STATUSES = ("executed", "closed")
APPROVED_IDEA_STATUSES = ("approved_pending", "approved")   # the only idea statuses that admit a real approval (step 5)


def review_blocked(rv: dict | None) -> bool:
    """The watcher treats a review as VETO (blocked) when validate_session.review_is_veto (recorded VETO, derived VETO
    from the rule results, or no results) OR when it fails INV-16 (validate_session.overall_errors: stored verdict /
    blocking ids / flag ids differ from risk_reference.overall(results)), e.g. 'tampered_pass': a stored PASS over a
    VETO, a hard UNKNOWN or a FLAG. Spec 1.1.5 ruling (fail closed, matches optpipe effective_verdict). Applied to every
    record the watcher reads, whatever its schema_version (the validator gates INV-16 as a record error to 1.1.3+; the
    watcher must still never act on a tampered older record). None (no review) is not blocked."""
    import validate_session as vs  # local import: the validator loads the schema at import time

    return rv is not None and (vs.review_is_veto(rv) or bool(vs.overall_errors(rv)))


# APPROVAL STATE IS READ ONLY FROM approvals[].status. There is no 'decision' field in the schema (approval objects have
# additionalProperties: false, so a record carrying 'decision' is schema-invalid); never read, trust or fall back to it.
def _when(v, what: str, errors: list | None, inv: str = "INV-06"):
    """aware datetime from an ISO string (or an aware datetime), else None plus an error appended to `errors`
    (spec 1.1.6, G5: naive -> '<inv>-TIME-NAIVE', unparsable / wrong type -> '<inv>-TIME-INVALID'). Never raises."""
    dt = v if isinstance(v, datetime) else None
    if dt is None and isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            dt = None
    if dt is not None and dt.tzinfo is not None and dt.utcoffset() is not None:
        return dt
    if errors is not None:
        if dt is not None:
            errors.append(f"{inv}-TIME-NAIVE {what} {v!r} has no UTC offset (naive times are forbidden; fail closed)")
        else:
            errors.append(f"{inv}-TIME-INVALID {what} {v!r} is not an ISO 8601 time with a UTC offset (fail closed)")
    return None


def _pair(a: dict, key: str):
    v = a.get(key)
    return v.get("ny") if isinstance(v, dict) else v


def in_window(a: dict, t: datetime, errors: list | None = None) -> bool:
    """Spec 1.1.6 (G4): an approval is valid at instant t only while approved_at < t < expires_at (open window; the
    same rule as INV-06 at filled_at). Naive / unparsable approval times -> False (fail closed) + an INV-06 error."""
    aid = a.get("approval_id")
    t0 = _when(_pair(a, "approved_at"), f"approval {aid} approved_at.ny", errors)
    t1 = _when(_pair(a, "expires_at"), f"approval {aid} expires_at.ny", errors)
    return t0 is not None and t1 is not None and t0 < t < t1


def real_approval(sess: dict, ticket_id: str, now: str | None = None, errors: list | None = None) -> dict | None:
    """The approval that makes `ticket_id` really approved, or None.

    The canonical approval field is approvals[].status (there is no 'decision' field). An approval counts only if
    status == 'active', it is for this ticket, it is inside its window at `now` (spec 1.1.6: approved_at < now <
    expires_at, open, instants compared; any time when now is None), it passes the 1.1.3 approval checks
    (validate_session.approval_errors: not a VETO review, same ticket, legs/qty/limit equal the ticket, every flag
    acknowledged) and (spec 1.1.5) its review passes INV-16 (validate_session.overall_errors: the stored overall
    verdict / ids equal the recomputed ones, 1.1.6: fail-closed recompute). 'expired', 'revoked', 'consumed' or no
    approval -> None (target_from_session then maps a revoked approval to 'rejected', the rest to hypothetical; an
    approval on an INV-16-inconsistent review makes the ticket 'vetoed').
    Spec 1.1.5 (step 5 of the precedence): the ticket's idea must be approved_pending or approved; an active approval on
    an idea that is proposed, simulated, executed, closed, ... or on a ticket with no idea is NEVER a real approval.
    Spec 1.1.6 (G5): a naive / unparsable `now` or approval time never raises: no approval is real (fail closed) and
    the INV-06-TIME-NAIVE / -INVALID message is appended to `errors` when given."""
    import validate_session as vs

    idea = next((i for i in sess.get("idea_log", []) if i.get("ticket_id") == ticket_id), None)
    if idea is None or idea.get("current_status") not in APPROVED_IDEA_STATUSES:
        return None
    tk = next((t for t in sess.get("tickets", []) if t["ticket_id"] == ticket_id), None)
    rvs = {r["review_id"]: r for r in sess.get("risk_reviews", [])}
    t = None
    if now is not None:
        t = _when(now, "now", errors)
        if t is None:
            return None
    for a in sess.get("approvals", []):
        if a.get("ticket_id") != ticket_id or a.get("status") != "active":
            continue
        if t is not None and not in_window(a, t, errors):
            continue
        rv = rvs.get(a.get("review_id"))
        if rv is None or tk is None or vs.approval_errors(a, rv, tk) or vs.overall_errors(rv):
            continue
        return a
    return None


def target_from_session(sess: dict, ticket_id: str, now: str | None = None) -> dict:
    """Watcher target for a ticket, derived from a session record (scope in watcher_rules.yaml / watcher_rules.md).

    Precedence (TARGET_PRECEDENCE, spec 1.1.5; first match wins):
      1. VETO first: the latest risk review of the ticket (by evaluated_at) is blocked (review_blocked: recorded VETO,
         derived VETO, no rule results, or INV-16 inconsistent, e.g. tampered_pass), or any approval of the ticket
         points at a blocked review, or the idea is 'vetoed'                          -> 'vetoed' (shadow only)
      2. Terminal lifecycle: the idea is 'executed' or 'closed'                       -> that status (not watched as a
         ticket; the trade is watched by the position rules). A revoked approval does not relabel it.
      3. Pre-execution idea (any other status, or no idea): the idea is 'rejected', or any approval of the ticket is
         'revoked' and there is no real approval (5)                                  -> 'rejected' (shadow only)
      4. a. idea 'approved_pending' / 'approved' / no idea: real approval (5)         -> 'approved_pending', approved
            otherwise (expired / consumed / active but not real / none)               -> 'approved_pending', hypothetical
         b. any other non-approved idea status (proposed, simulated, ...)             -> that status (not watched)
      5. A real approval (real_approval) needs the idea approved_pending / approved AND status 'active', inside its
         window, INV-05 clean and INV-16 clean. An active approval on a proposed / simulated idea (or no idea) is
         never real.

    Spec 1.1.6: the returned dict gains an `errors` list (INV-06-TIME-NAIVE / -INVALID for `now` or approval times,
    INV-02-TIME-NAIVE / -INVALID for a review's evaluated_at) only when a time could not be used; otherwise the output
    is exactly the 1.1.5 shape. A review whose evaluated_at is unusable cannot be ordered, so the ticket is 'vetoed'
    (fail closed); an unusable `now` or approval time means no real approval."""
    errors: list[str] = []
    mine_rv = [r for r in sess.get("risk_reviews", []) if r["ticket_id"] == ticket_id]
    when = {id(r): _when(_pair(r, "evaluated_at"), f"review {r.get('review_id')} evaluated_at.ny", errors, "INV-02")
            for r in mine_rv}
    unordered = any(v is None for v in when.values())
    reviews = [] if unordered else sorted(mine_rv, key=lambda r: when[id(r)])
    rv_by_id = {r["review_id"]: r for r in sess.get("risk_reviews", [])}
    idea = next((i for i in sess.get("idea_log", []) if i.get("ticket_id") == ticket_id), None)
    istatus = idea["current_status"] if idea else None
    mine = [a for a in sess.get("approvals", []) if a.get("ticket_id") == ticket_id]

    base = {"kind": "ticket", "id": ticket_id, "ticket_id": ticket_id}
    shadow = {"approved": False, "hypothetical": True, "approval_id": None}

    def out(d: dict) -> dict:
        return {**d, "errors": errors} if errors else d
    # 1. VETO first (INV-16-inconsistent reviews count as VETO, spec 1.1.5; an unorderable review, spec 1.1.6)
    if unordered or (reviews and review_blocked(reviews[-1])) or istatus == "vetoed" or any(
            review_blocked(rv_by_id.get(a.get("review_id"))) for a in mine):
        return out({**base, "status": "vetoed", **shadow})
    # 2. terminal lifecycle keeps its status (positions are watched by the position rules)
    if istatus in TERMINAL_IDEA_STATUSES:
        return out({**base, "status": istatus, **shadow})
    # 3. pre-execution: idea rejected, or a revoked approval with no real approval
    a = real_approval(sess, ticket_id, now, errors)
    if istatus == "rejected" or (a is None and any(x.get("status") == "revoked" for x in mine)):
        return out({**base, "status": "rejected", **shadow})
    # 4a. watched ticket: real or hypothetical
    if istatus in (None, *APPROVED_IDEA_STATUSES):
        return out({**base, "status": "approved_pending", "approved": a is not None, "hypothetical": a is None,
                    "approval_id": a["approval_id"] if a else None})
    # 4b. any other non-approved idea status passes through (never really approved)
    return out({**base, "status": istatus, **shadow})
