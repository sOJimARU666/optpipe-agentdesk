"""Risk Officer engine: a pure, deterministic implementation of Doodle's risk_rules.yaml (R01-R11).

evaluate(ReviewInput, Ruleset) -> RiskEvaluation. No clock, no I/O, no network: every time comes from
the input (evaluated_at). Market hours / DTE / windows are computed in America/New_York only.
Semantics are ported from spec/risk_reference.py; tests/test_risk_parity.py proves agreement.
Owner: Doodle (rules) / Poodle (code).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from .review_input import REvent, RField, RLeg, ReviewInput
from .ruleset import MarketHours, RuleDef, Ruleset

NY = ZoneInfo("America/New_York")
PREC = {"PASS": 0, "FLAG": 1, "UNKNOWN": 2, "VETO": 3}


def D(x) -> Decimal:
    return Decimal(str(x))


def combine(results) -> str:
    results = list(results)
    return max(results, key=PREC.__getitem__) if results else "PASS"


def _ts(v) -> datetime:
    dt = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp forbidden: {v}")
    return dt


def _iso(v) -> str:
    return v.isoformat() if isinstance(v, datetime) else str(v)


class RuleOutcome(BaseModel):
    """One rule result; same fields as session.schema.json risk_reviews[].results[]."""
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    severity: str
    result: str
    measured: Any
    threshold: Any
    reason: str
    per_leg: Optional[list[dict[str, Any]]] = None
    placeholder: Optional[bool] = None


class Overall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str
    blocking_rule_ids: list[str]
    flag_rule_ids: list[str]
    approval_required: bool = True


class RiskEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[RuleOutcome]
    overall: Overall
    market_open_at_evaluation: bool


class Calendar:
    def __init__(self, mh: MarketHours):
        self.open, self.close, self.early = mh.open, mh.close, mh.early_close
        self.holidays, self.early_closes = set(mh.holidays), set(mh.early_closes)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def close_dt(self, d: date) -> datetime:
        return datetime.combine(d, self.early if d in self.early_closes else self.close, NY)

    def is_open(self, t: datetime) -> bool:
        n = t.astimezone(NY)
        if not self.is_trading_day(n.date()):
            return False
        return datetime.combine(n.date(), self.open, NY) <= n < self.close_dt(n.date())

    def prev_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d


def leg_mid(leg: RLeg) -> tuple[Optional[Decimal], str]:
    if not (leg.bid.usable and leg.ask.usable):
        return None, "bid/ask unusable"
    bid, ask = D(leg.bid.value), D(leg.ask.value)
    if bid < 0 or ask <= 0 or ask < bid:
        return None, f"invalid quote bid={bid} ask={ask}"
    mid = (bid + ask) / 2
    if mid == 0:
        return None, "mid is zero"
    return mid, ""


class Ctx:
    def __init__(self, inp: ReviewInput, rs: Ruleset):
        self.inp, self.cfg, self.raw = inp, rs.config, rs.raw_config
        self.cal = Calendar(rs.config.market_hours)
        self.t = inp.evaluated_at
        start = max(self.t, inp.ticket.hold_window_start)
        end = inp.ticket.hold_window_end
        if inp.legs:
            end = min(end, min(self.cal.close_dt(l.expiry) for l in inp.legs))
        self.w_start, self.w_end = start, end

    def in_window(self, a: datetime, b: datetime) -> bool:
        return a <= self.w_end and b >= self.w_start

    def event_interval(self, ev: REvent):
        f = ev.scheduled_at
        if not f.usable:
            return None
        s = _ts(f.value).astimezone(NY)
        d = s.date()
        if ev.timing == "bmo":
            p = datetime.combine(d, time(9, 30), NY); return p, p
        if ev.timing == "amc":
            p = datetime.combine(d, time(16, 0), NY); return p, p
        if ev.timing in ("during_market", "at_time"):
            return s, s
        return datetime.combine(d, time(0, 0), NY), datetime.combine(d, time(23, 59, 59), NY)

    def exdiv_exposure(self, ev: REvent):
        if not ev.scheduled_at.usable:
            return None
        ex = _ts(ev.scheduled_at.value).astimezone(NY).date()
        return self.cal.close_dt(self.cal.prev_trading_day(ex))

    def coverage_ok(self, kind: str, underlying: Optional[str]) -> tuple[bool, str]:
        need = self.w_end.astimezone(NY).date()
        for c in self.inp.coverage:
            if c.kind == kind and c.underlying == underlying:
                f = c.checked_through
                if not f.usable:
                    return False, f"{kind} coverage {f.status}"
                if date.fromisoformat(str(f.value)) < need:
                    return False, f"{kind} checked only through {f.value} < window end {need}"
                return True, ""
        return False, f"no {kind} coverage for {underlying or 'macro'}"


def _res(rule: RuleDef, result, measured, threshold, reason, per_leg=None) -> RuleOutcome:
    return RuleOutcome(rule_id=rule.id, severity=rule.severity, result=result, measured=measured, threshold=threshold,
                       reason=reason, per_leg=per_leg, placeholder=True if rule.placeholder else None)


def payoff_max_loss(inp: ReviewInput, c: Ctx) -> dict:
    """Max loss at the limit price from the piecewise-linear expiry payoff (exact Decimal)."""
    legs, tk = inp.legs, inp.ticket
    if not legs:
        return {"error": "no legs"}
    if len({l.expiry for l in legs}) > 1:
        return {"error": "multi-expiry payoff unsupported in v1"}
    if tk.limit_price.amount is None:
        return {"error": "limit price missing"}
    qty, mult = D(tk.quantity), D(c.cfg.contract_multiplier)
    premium = D(tk.limit_price.amount) if tk.limit_price.price_type == "debit" else -D(tk.limit_price.amount)
    sign = lambda l: 1 if l.side == "buy" else -1
    call_slope = sum(sign(l) * l.ratio for l in legs if l.type == "call")
    fees = D(c.raw["fees_per_contract_usd"]) * qty * sum(D(l.ratio) for l in legs)
    if call_slope < 0:
        return {"max_loss_usd": None, "unlimited": True}

    def pnl(S: Decimal) -> Decimal:
        tot = D(0)
        for l in legs:
            k = D(l.strike)
            intr = max(D(0), S - k) if l.type == "call" else max(D(0), k - S)
            tot += sign(l) * D(l.ratio) * intr
        return (tot - premium) * mult * qty - fees

    worst = min(pnl(S) for S in [D(0)] + [D(l.strike) for l in legs])
    return {"max_loss_usd": float(max(D(0), -worst)), "unlimited": False}


# ----------------------------------------------------------------------------- rules
def r01(rule, c: Ctx):
    thr = {"quote_max_age_seconds": c.raw["quote_max_age_seconds"]}
    if not c.cal.is_open(c.t):
        return _res(rule, "VETO", {"market_open": False, "evaluated_at_ny": c.t.astimezone(NY).isoformat()}, thr,
                    "US regular session closed at evaluated_at (America/New_York); quotes cannot be live")
    items: list[tuple[str, RField]] = [("underlying.last", c.inp.underlying.last)]
    for l in c.inp.legs:
        items += [(f"{l.leg_id}.bid", l.bid), (f"{l.leg_id}.ask", l.ask)]
    out, ages, why = [], [], []
    for name, f in items:
        if not f.usable or not f.as_of:
            out.append("UNKNOWN"); why.append(f"{name} unusable"); continue
        age = (c.t - f.as_of).total_seconds()
        if age < -c.cfg.quote_future_tolerance_seconds:
            out.append("UNKNOWN"); why.append(f"{name} as_of in future"); continue
        ages.append(age)
        if f.status == "stale":
            out.append("VETO"); why.append(f"{name} stale")
        elif age > c.cfg.quote_max_age_seconds:
            out.append("VETO"); why.append(f"{name} age {age/60:.1f} min")
        else:
            out.append("PASS")
    m = {"market_open": True, "max_age_min": round(max(ages) / 60, 2) if ages else None}
    return _res(rule, combine(out), m, thr, "; ".join(why) or "all quotes <= 15 min old, market open")


def r02(rule, c: Ctx):
    lim = D(c.raw["max_spread_pct_of_mid"])
    per, out = [], []
    for l in c.inp.legs:
        mid, why = leg_mid(l)
        if mid is None:
            per.append({"leg_id": l.leg_id, "result": "UNKNOWN", "measured": None, "reason": why}); out.append("UNKNOWN"); continue
        w = (D(l.ask.value) - D(l.bid.value)) / mid
        r = "PASS" if w <= lim else "VETO"
        per.append({"leg_id": l.leg_id, "result": r, "measured": float(round(w, 4))}); out.append(r)
    reason = ", ".join(f"{p['leg_id']} {p['measured'] if p['measured'] is not None else 'n/a'} {p['result']}" for p in per)
    return _res(rule, combine(out), {p["leg_id"]: p["measured"] for p in per}, {"max_spread_pct_of_mid": float(lim)}, reason, per)


def r03(rule, c: Ctx):
    mn = c.raw["min_open_interest"]
    per, out = [], []
    for l in c.inp.legs:
        f = l.open_interest
        if not f.usable:
            per.append({"leg_id": l.leg_id, "result": "UNKNOWN", "measured": None}); out.append("UNKNOWN"); continue
        r = "PASS" if f.value >= mn else "VETO"
        per.append({"leg_id": l.leg_id, "result": r, "measured": f.value}); out.append(r)
    reason = ", ".join(f"{p['leg_id']} OI {p['measured']} {p['result']}" for p in per)
    return _res(rule, combine(out), {p["leg_id"]: p["measured"] for p in per}, {"min_open_interest": mn}, reason, per)


def r04(rule, c: Ctx):
    lim = c.raw.get("per_trade_max_loss_usd")
    thr = {"per_trade_max_loss_usd": lim}
    ml = payoff_max_loss(c.inp, c)
    if ml.get("error") or lim is None:
        return _res(rule, "UNKNOWN", None, thr, ml.get("error") or "per-trade limit not configured")
    if ml["unlimited"]:
        return _res(rule, "VETO", {"unlimited": True, "max_loss_usd": None}, thr, "unlimited loss (net short calls)")
    r = "PASS" if D(ml["max_loss_usd"]) <= D(lim) else "VETO"
    tail = " (placeholder pending Eugene)" if "per_trade_max_loss_usd" in c.cfg.placeholders else ""
    sep = " at limit vs " if tail else " vs "
    return _res(rule, r, {"unlimited": False, "max_loss_usd": ml["max_loss_usd"]}, thr,
                f"max loss ${ml['max_loss_usd']:.2f}{sep}${lim}{tail}")


def r05(rule, c: Ctx):
    lp = c.inp.ticket.limit_price
    basis = c.cfg.net_price_basis
    thr = {"limit": lp.amount, "price_type": lp.price_type, "basis": basis}
    net = D(0)
    for l in c.inp.legs:
        mid, why = leg_mid(l)
        if mid is None:
            return _res(rule, "UNKNOWN", None, thr, f"{l.leg_id}: {why}")
        px = (D(l.ask.value) if l.side == "buy" else D(l.bid.value)) if basis == "natural" else mid
        net += (1 if l.side == "buy" else -1) * D(l.ratio) * px
    limit = D(lp.amount)
    if lp.price_type == "debit":
        r, reason = ("PASS" if net <= limit else "VETO"), f"net debit {net} vs limit {limit}"
    else:
        r, reason = ("PASS" if -net >= limit else "VETO"), f"net credit {-net} vs limit {limit}"
    return _res(rule, r, {"net": float(net)}, thr, reason)


def r06(rule, c: Ctx):
    thr = {"min_dte": c.cfg.min_dte}
    if not c.inp.legs:
        return _res(rule, "UNKNOWN", None, thr, "no legs")
    today = c.t.astimezone(NY).date()
    dte = min((l.expiry - today).days for l in c.inp.legs)
    return _res(rule, "PASS" if dte >= c.cfg.min_dte else "VETO", {"dte": dte}, thr, f"{dte} DTE (NY date {today})")


def r07(rule, c: Ctx):
    u = c.inp.ticket.underlying
    intended = set(c.inp.ticket.intended_event_ids)
    out, why, hits = [], [], []
    ok, msg = c.coverage_ok("earnings", u)
    if not ok:
        out.append("UNKNOWN"); why.append(msg)
    for ev in c.inp.events:
        if ev.kind != "earnings" or ev.underlying != u:
            continue
        iv = c.event_interval(ev)
        if iv is None:
            out.append("UNKNOWN"); why.append(f"{ev.event_id} date unusable"); continue
        if c.in_window(*iv):
            hits.append(ev.event_id)
            if ev.event_id in intended:
                out.append("FLAG"); why.append(f"{ev.event_id} in window (intended)")
            else:
                out.append("VETO"); why.append(f"{ev.event_id} in window, not intended")
    out.append("PASS")
    return _res(rule, combine(out), {"events_in_window": hits, "window": [c.w_start.isoformat(), c.w_end.isoformat()]},
                {"intended_event_ids": sorted(intended)}, "; ".join(why) or "no earnings in hold window")


def r08(rule, c: Ctx):
    kinds = c.raw["macro_event_kinds"]
    out, why, hits = ["PASS"], [], []
    for k in kinds:
        ok, msg = c.coverage_ok(k, None)
        if not ok:
            out.append("UNKNOWN"); why.append(msg)
    for ev in c.inp.events:
        if ev.kind not in kinds:
            continue
        iv = c.event_interval(ev)
        if iv is None:
            out.append("UNKNOWN"); why.append(f"{ev.event_id} date unusable"); continue
        if c.in_window(*iv):
            out.append("FLAG"); hits.append(ev.event_id); why.append(f"{ev.kind} {ev.event_id} in window")
    return _res(rule, combine(out), {"events_in_window": hits}, {"macro_event_kinds": kinds},
                "; ".join(why) or "no macro events in window")


def r09(rule, c: Ctx):
    u = c.inp.ticket.underlying
    shorts = [l for l in c.inp.legs if l.side == "sell"]
    thr = {"min_put_extrinsic": float(D(c.raw["min_put_extrinsic"]))}
    if not shorts:
        return _res(rule, "PASS", None, thr, "no short legs", [])
    S = D(c.inp.underlying.last.value) if c.inp.underlying.last.usable else None
    per, out, exdivs, exdiv_unknown = [], [], [], []
    if any(l.type == "call" for l in shorts):
        ok, msg = c.coverage_ok("ex_dividend", u)
        if not ok:
            exdiv_unknown.append(msg)
        for ev in c.inp.events:
            if ev.kind == "ex_dividend" and ev.underlying == u:
                inst = c.exdiv_exposure(ev)
                if inst is None or ev.dividend_amount is None or not ev.dividend_amount.usable:
                    exdiv_unknown.append(f"{ev.event_id} date/dividend unusable"); continue
                if c.w_start <= inst <= c.w_end:
                    exdivs.append((ev, D(ev.dividend_amount.value)))
    for l in shorts:
        K, lid = D(l.strike), l.leg_id
        mid, _ = leg_mid(l)
        if l.type == "call":
            items = ["UNKNOWN"] if exdiv_unknown else []
            m: dict[str, Any] = {"exdiv_in_window": [e.event_id for e, _ in exdivs]}
            for ev, div in exdivs:
                if S is None or mid is None:
                    items.append("UNKNOWN"); continue
                intr = max(D(0), S - K); ext = mid - intr
                m.update({"itm": S > K, "extrinsic": float(ext), "dividend": float(div)})
                items.append("VETO" if (S > K or ext < div) else "FLAG")
            per.append({"leg_id": lid, "result": combine(items) if items else "PASS", "measured": m,
                        "reason": "; ".join(exdiv_unknown) or ("ex-div in window" if exdivs else "no ex-div in window")})
        else:
            if S is None or mid is None:
                per.append({"leg_id": lid, "result": "UNKNOWN", "measured": None, "reason": "spot or quote unusable"})
                out.append("UNKNOWN"); continue
            intr = max(D(0), K - S); ext = mid - intr
            r = "FLAG" if (K > S and ext < D(c.raw["min_put_extrinsic"])) else "PASS"
            per.append({"leg_id": lid, "result": r, "measured": {"itm": K > S, "extrinsic": float(ext)}})
        out.append(per[-1]["result"])
    return _res(rule, combine(out), {p["leg_id"]: p["measured"] for p in per}, thr,
                "; ".join(f"{p['leg_id']} {p['result']}" + (f" ({p['reason']})" if p.get("reason") else "") for p in per), per)


def r10(rule, c: Ctx):
    cur, hist = c.inp.iv.iv30_current, c.inp.iv.iv30_history
    thr = {"iv_rank_high": float(D(c.raw["iv_rank_high"])), "iv_rank_low": float(D(c.raw["iv_rank_low"])),
           "min_obs": c.raw["iv_min_history_obs"]}
    if not (cur.usable and hist.usable):
        return _res(rule, "UNKNOWN", None, thr, "IV30 current or history unavailable")
    h = [D(x) for x in hist.value]
    if len(h) < c.cfg.iv_min_history_obs:
        return _res(rule, "UNKNOWN", {"obs": len(h)}, thr, f"only {len(h)} history observations")
    lo, hi, cv = min(h), max(h), D(cur.value)
    rank = D("0.5") if hi == lo else (cv - lo) / (hi - lo)
    pt = c.inp.ticket.limit_price.price_type
    r = "FLAG" if (pt == "debit" and rank > c.cfg.iv_rank_high) or (pt == "credit" and rank < c.cfg.iv_rank_low) else "PASS"
    return _res(rule, r, {"iv_rank": float(round(rank, 4)), "iv30": float(cv), "min": float(lo), "max": float(hi)}, thr,
                f"IV rank {float(rank):.2f} ({pt})")


def r11(rule, c: Ctx):
    thr = {k: c.raw.get(k) for k in ("max_underlying_risk_usd", "max_sector_risk_usd", "max_total_open_risk_usd",
                                     "max_open_positions")}
    op = c.inp.open_positions
    if not op.usable:
        return _res(rule, "UNKNOWN", None, thr, "open positions unknown")
    ml = payoff_max_loss(c.inp, c)
    if ml.get("error") or ml.get("unlimited"):
        return _res(rule, "FLAG", {"ticket_max_loss": None}, thr, "ticket max loss unlimited/unknown")
    u, t = c.inp.ticket.underlying, D(ml["max_loss_usd"])
    same = sum((D(p["max_loss_usd"]) for p in op.value if p["underlying"] == u), D(0)) + t
    total = sum((D(p["max_loss_usd"]) for p in op.value), D(0)) + t
    n = len(op.value) + 1
    flag = same > c.cfg.max_underlying_risk_usd or total > c.cfg.max_total_open_risk_usd or n > c.cfg.max_open_positions
    measured: dict[str, Any] = {"same_underlying_usd": float(same), "total_usd": float(total), "count": n}
    why: list[str] = []
    if c.cfg.max_sector_risk_usd is not None:          # ruleset 1.1.0 sector cap; null = not checked
        smap = c.cfg.sector_map
        sec = smap.get(u)
        sector_of = lambda p: p.get("sector", smap.get(p["underlying"]))       # noqa: E731
        unmapped = sorted({p["underlying"] for p in op.value if sector_of(p) is None} | ({u} if sec is None else set()))
        if unmapped:
            return _res(rule, "UNKNOWN", {**measured, "sector": sec, "same_sector_usd": None}, thr,
                        f"no sector mapping for {', '.join(unmapped)}")
        same_sec = sum((D(p["max_loss_usd"]) for p in op.value if sector_of(p) == sec), D(0)) + t
        measured.update(sector=sec, same_sector_usd=float(same_sec))
        flag = flag or same_sec > c.cfg.max_sector_risk_usd
        why.append(f"sector {sec} ${same_sec}")
    tag = " (placeholder limits pending Eugene)" if "max_underlying_risk_usd" in c.cfg.placeholders else ""
    return _res(rule, "FLAG" if flag else "PASS", measured, thr,
                f"same-underlying ${same}, {', '.join(why) + ', ' if why else ''}total ${total}, positions {n}{tag}")


VERTICALS = {  # strategy -> (option type, price_type, which strike is bought: low|high)
    "bull_call_debit_spread": ("call", "debit", "low"), "bear_put_debit_spread": ("put", "debit", "high"),
    "bull_put_credit_spread": ("put", "credit", "low"), "bear_call_credit_spread": ("call", "credit", "high"),
}


def shape_errors(strategy: str, legs: list[dict], price_type: Optional[str]) -> list[str]:
    """Reasons the legs do not form the declared strategy ([] = they match). Port of Doodle's _shape_errors."""
    if strategy in VERTICALS:
        typ, pt, bought = VERTICALS[strategy]
        if len(legs) != 2:
            return [f"{strategy} needs 2 legs, got {len(legs)}"]
        a, b = legs
        errs = []
        if {a["type"], b["type"]} != {typ}:
            errs.append(f"{strategy} legs must both be {typ}s")
        if a["expiry"] != b["expiry"]:
            errs.append("vertical legs must share one expiry")
        if a["ratio"] != b["ratio"]:
            errs.append("vertical legs must have equal ratio")
        if {a["side"], b["side"]} != {"buy", "sell"}:
            errs.append("vertical needs one buy and one sell leg")
        if a["strike"] == b["strike"]:
            errs.append("vertical strikes must differ")
        if not errs:
            lo, hi = sorted(legs, key=lambda l: l["strike"])
            if (lo if bought == "low" else hi)["side"] != "buy":
                errs.append(f"{strategy} must buy the {bought}er strike")
        if price_type != pt:
            errs.append(f"{strategy} must be priced as {pt}, ticket says {price_type}")
        return errs
    if strategy in ("long_call", "long_put"):
        typ = strategy.split("_")[1]
        ok = len(legs) == 1 and legs[0]["type"] == typ and legs[0]["side"] == "buy" and price_type == "debit"
        return [] if ok else [f"{strategy} must be a single bought {typ} priced as debit"]
    if strategy == "iron_condor":
        puts = [l for l in legs if l["type"] == "put"]
        calls = [l for l in legs if l["type"] == "call"]
        if len(legs) != 4 or len(puts) != 2 or len(calls) != 2:
            return ["iron_condor needs 2 puts + 2 calls"]
        errs = (shape_errors("bull_put_credit_spread", puts, "credit")
                + shape_errors("bear_call_credit_spread", calls, "credit"))
        if len({l["expiry"] for l in legs}) > 1:
            errs.append("iron_condor legs must share one expiry")
        if price_type != "credit":
            errs.append("iron_condor must be priced as credit")
        return errs
    return [f"no shape definition for strategy {strategy!r}"]


def r12(rule, c: Ctx):
    tk = c.inp.ticket
    legs = [{"type": l.type, "side": l.side, "strike": l.strike, "expiry": l.expiry, "ratio": l.ratio} for l in c.inp.legs]
    allowed = list(c.cfg.allowed_structures)
    thr = {"allowed_structures": allowed}
    if not legs or not tk.strategy:
        return _res(rule, "UNKNOWN", None, thr, "no legs" if not legs else "ticket strategy missing")
    net = {typ: sum((1 if l["side"] == "buy" else -1) * l["ratio"] for l in legs if l["type"] == typ)
           for typ in ("call", "put")}
    naked = [typ for typ, v in net.items() if v < 0]
    measured = {"strategy": tk.strategy, "naked_short": naked, "defined_risk": not naked}
    if naked:
        return _res(rule, "VETO", measured, thr,
                    f"naked short {'/'.join(naked)} (more contracts sold than bought): not defined-risk")
    if tk.strategy not in allowed:
        return _res(rule, "VETO", measured, thr, f"structure {tk.strategy} not in allowed_structures")
    errs = shape_errors(tk.strategy, legs, tk.limit_price.price_type)
    if errs:
        return _res(rule, "VETO", measured, thr, "; ".join(errs))
    return _res(rule, "PASS", measured, thr, f"{tk.strategy}: defined-risk, legs match")


def r13(rule, c: Ctx):
    lo, hi = c.cfg.hold_min_days, c.cfg.hold_max_days
    thr = {"hold_min_days": lo, "hold_max_days": hi}
    legs, tk = c.inp.legs, c.inp.ticket
    if not legs or lo is None:
        return _res(rule, "UNKNOWN", None, thr, "no legs" if not legs else "hold_min_days not configured")
    today = c.t.astimezone(NY).date()
    start = max(c.t, tk.hold_window_start).astimezone(NY).date()
    stop = tk.hold_window_end
    hold = (stop.astimezone(NY).date() - start).days
    dte = min((l.expiry - today).days for l in legs)
    first_close = min(c.cal.close_dt(l.expiry) for l in legs)
    why = []
    if stop > first_close:
        why.append(f"time stop {stop.astimezone(NY).isoformat()} after expiry close {first_close.isoformat()}")
    for name, v in (("time stop", hold), ("DTE", dte)):
        if v < lo or (hi is not None and v > hi):
            why.append(f"{name} {v} d outside {lo}-{hi if hi is not None else 'inf'} d")
    return _res(rule, "VETO" if why else "PASS", {"hold_days": hold, "dte": dte}, thr,
                "; ".join(why) or f"time stop in {hold} d, DTE {dte} (NY date {today})")


RULE_FUNCS: dict[str, Callable[[RuleDef, Ctx], RuleOutcome]] = {
    "R01_QUOTE_FRESHNESS": r01, "R02_BID_ASK_WIDTH": r02, "R03_OPEN_INTEREST": r03, "R04_MAX_LOSS": r04,
    "R05_NET_PRICE_VS_LIMIT": r05, "R06_MIN_DTE": r06, "R07_EARNINGS_IN_WINDOW": r07, "R08_MACRO_EVENTS": r08,
    "R09_EARLY_ASSIGNMENT": r09, "R10_IV_VS_HISTORY": r10, "R11_CONCENTRATION": r11,
    "R12_DEFINED_RISK_STRUCTURE": r12, "R13_HOLD_PERIOD": r13}


def overall(results: list[RuleOutcome]) -> Overall:
    blocking = [r.rule_id for r in results if r.result == "VETO" or (r.result == "UNKNOWN" and r.severity == "hard")]
    flags = [r.rule_id for r in results if r.result == "FLAG" or (r.result == "UNKNOWN" and r.severity == "soft")]
    return Overall(verdict="VETO" if blocking else ("FLAG" if flags else "PASS"),
                   blocking_rule_ids=blocking, flag_rule_ids=flags, approval_required=True)


def evaluate(inp: ReviewInput | dict, ruleset: Ruleset, config_overrides: dict | None = None,
             profile: str | None = None) -> RiskEvaluation:
    """Pure function: same input + ruleset -> same output. Unknown rule ids fail loudly.
    cfg = defaults <- profile overrides <- per-call overrides (as Doodle's risk_reference.evaluate)."""
    inp = ReviewInput.model_validate(inp)
    rs = ruleset.with_profile(profile).with_overrides(config_overrides)
    c = Ctx(inp, rs)
    missing = [r.id for r in rs.rules if r.id not in RULE_FUNCS]
    if missing:
        raise NotImplementedError(f"rules in ruleset without an implementation: {missing}")
    results = [RULE_FUNCS[r.id](r, c) for r in rs.rules]
    return RiskEvaluation(results=results, overall=overall(results), market_open_at_evaluation=c.cal.is_open(c.t))
