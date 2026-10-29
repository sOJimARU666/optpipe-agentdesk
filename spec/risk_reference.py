"""Tiny reference implementation of risk_rules.yaml (v1.0.0).

Purpose: prove the rules are unambiguous. Not production code; Poodle should port the
semantics, and keep tests/test_risk_reference.py green against its own engine.

Input ("review input") shape: see `fixture` in risk_rules.yaml. Every sourced datum is a
Field dict {value, status, as_of}. `inputs_from_session()` builds it from a session record
(session.schema.json) with point-in-time visibility.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

SPEC_DIR = Path(__file__).resolve().parent
NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")
PREC = {"PASS": 0, "FLAG": 1, "UNKNOWN": 2, "VETO": 3}


def load_rules(path: Path = SPEC_DIR / "risk_rules.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ----------------------------------------------------------------- helpers
def D(x) -> Decimal:
    return Decimal(str(x))


def usable(f) -> bool:
    return isinstance(f, dict) and f.get("status") in ("ok", "stale") and f.get("value") is not None


def ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp forbidden: {s}")
    return dt


def combine(results) -> str:
    results = list(results)
    return max(results, key=PREC.__getitem__) if results else "PASS"


def res(rule, result, measured, threshold, reason, per_leg=None):
    out = {"rule_id": rule["id"], "severity": rule["severity"], "result": result,
           "measured": measured, "threshold": threshold, "reason": reason}
    if per_leg is not None:
        out["per_leg"] = per_leg
    if rule.get("placeholder"):
        out["placeholder"] = True
    return out


class Calendar:
    def __init__(self, mh: dict):
        assert mh["timezone"] == "America/New_York"
        self.open = time.fromisoformat(mh["open"])
        self.close = time.fromisoformat(mh["close"])
        self.early = time.fromisoformat(mh["early_close"])
        self.holidays = {date.fromisoformat(d) for d in mh["holidays"]}
        self.early_closes = {date.fromisoformat(d) for d in mh["early_closes"]}

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def close_dt(self, d: date) -> datetime:
        return datetime.combine(d, self.early if d in self.early_closes else self.close, NY)

    def is_open(self, t: datetime) -> bool:
        n = t.astimezone(NY)
        if not self.is_trading_day(n.date()):
            return False
        return datetime.combine(n.date(), self.open, NY) <= n < self.close_dt(n.date())

    EXTENDED_OPEN = time(4, 0)

    def is_closed(self, t: datetime) -> bool:
        """Run phase 'closed' (schema v1.1): weekend / NYSE holiday, or before 04:00 NY,
        or at/after the regular close (13:00 on early-close days). Computed in America/New_York."""
        n = t.astimezone(NY)
        if not self.is_trading_day(n.date()):
            return True
        return n < datetime.combine(n.date(), self.EXTENDED_OPEN, NY) or n >= self.close_dt(n.date())

    def prev_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d


def leg_mid(leg):
    """-> (mid Decimal | None, reason)"""
    b, a = leg.get("bid"), leg.get("ask")
    if not (usable(b) and usable(a)):
        return None, "bid/ask unusable"
    bid, ask = D(b["value"]), D(a["value"])
    if bid < 0 or ask <= 0 or ask < bid:
        return None, f"invalid quote bid={bid} ask={ask}"
    mid = (bid + ask) / 2
    if mid == 0:
        return None, "mid is zero"
    return mid, ""


class Ctx:
    def __init__(self, inp: dict, cfg: dict):
        self.inp, self.cfg = inp, cfg
        self.cal = Calendar(cfg["market_hours"])
        self.t = ts(inp["evaluated_at"])
        tk = inp["ticket"]
        start = max(self.t, ts(tk["hold_window_start"]))
        expiry_close = min(self.cal.close_dt(date.fromisoformat(l["expiry"])) for l in inp["legs"]) if inp["legs"] else None
        end = ts(tk["hold_window_end"])
        if expiry_close is not None:
            end = min(end, expiry_close)
        self.w_start, self.w_end = start, end

    def in_window(self, a: datetime, b: datetime) -> bool:
        return a <= self.w_end and b >= self.w_start

    def event_interval(self, ev):
        """earnings / macro interval -> (a, b) or None if unusable"""
        f = ev.get("scheduled_at")
        if not usable(f):
            return None
        s = ts(f["value"]).astimezone(NY)
        d = s.date()
        timing = ev.get("timing", "unknown")
        if timing == "bmo":
            p = datetime.combine(d, time(9, 30), NY); return p, p
        if timing == "amc":
            p = datetime.combine(d, time(16, 0), NY); return p, p
        if timing in ("during_market", "at_time"):
            return s, s
        return datetime.combine(d, time(0, 0), NY), datetime.combine(d, time(23, 59, 59), NY)

    def exdiv_exposure(self, ev):
        f = ev.get("scheduled_at")
        if not usable(f):
            return None
        ex = ts(f["value"]).astimezone(NY).date()
        return self.cal.close_dt(self.cal.prev_trading_day(ex))

    def coverage_ok(self, kind, underlying) -> tuple[bool, str]:
        need = self.w_end.astimezone(NY).date()
        for c in self.inp.get("coverage", []):
            if c["kind"] == kind and c.get("underlying") == underlying:
                f = c.get("checked_through")
                if not usable(f):
                    return False, f"{kind} coverage {f.get('status') if f else 'absent'}"
                if date.fromisoformat(f["value"]) < need:
                    return False, f"{kind} checked only through {f['value']} < window end {need}"
                return True, ""
        return False, f"no {kind} coverage for {underlying or 'macro'}"


def payoff_max_loss(inp, cfg):
    """-> dict(max_loss_usd | None, unlimited, error)"""
    legs, tk = inp["legs"], inp["ticket"]
    if not legs:
        return {"error": "no legs"}
    if len({l["expiry"] for l in legs}) > 1:
        return {"error": "multi-expiry payoff unsupported in v1"}
    lp = tk.get("limit_price") or {}
    if lp.get("amount") is None:
        return {"error": "limit price missing"}
    qty = D(tk["quantity"]); mult = D(cfg["contract_multiplier"])
    premium = D(lp["amount"]) if lp["price_type"] == "debit" else -D(lp["amount"])
    sign = lambda l: 1 if l["side"] == "buy" else -1
    call_slope = sum(sign(l) * l["ratio"] for l in legs if l["type"] == "call")
    fees = D(cfg["fees_per_contract_usd"]) * qty * sum(D(l["ratio"]) for l in legs)
    if call_slope < 0:
        return {"max_loss_usd": None, "unlimited": True}
    def pnl(S):
        tot = D(0)
        for l in legs:
            k = D(l["strike"])
            intr = max(D(0), S - k) if l["type"] == "call" else max(D(0), k - S)
            tot += sign(l) * D(l["ratio"]) * intr
        return (tot - premium) * mult * qty - fees
    pts = [D(0)] + [D(l["strike"]) for l in legs]
    worst = min(pnl(S) for S in pts)
    return {"max_loss_usd": float(max(D(0), -worst)), "unlimited": False}


# ------------------------------------------------------------------- rules
def r01(rule, c: Ctx):
    cfg, inp = c.cfg, c.inp
    thr = {"quote_max_age_seconds": cfg["quote_max_age_seconds"]}
    if not c.cal.is_open(c.t):
        return res(rule, "VETO", {"market_open": False, "evaluated_at_ny": c.t.astimezone(NY).isoformat()}, thr,
                   "US regular session closed at evaluated_at (America/New_York); quotes cannot be live")
    items = [("underlying.last", inp["underlying"].get("last"), None)]
    for l in inp["legs"]:
        items += [(f"{l['leg_id']}.bid", l.get("bid"), l["leg_id"]), (f"{l['leg_id']}.ask", l.get("ask"), l["leg_id"])]
    out, ages, why = [], [], []
    for name, f, _ in items:
        if not usable(f) or not f.get("as_of"):
            out.append("UNKNOWN"); why.append(f"{name} unusable"); continue
        age = (c.t - ts(f["as_of"])).total_seconds()
        if age < -cfg["quote_future_tolerance_seconds"]:
            out.append("UNKNOWN"); why.append(f"{name} as_of in future"); continue
        ages.append(age)
        if f["status"] == "stale":
            out.append("VETO"); why.append(f"{name} stale")
        elif age > cfg["quote_max_age_seconds"]:
            out.append("VETO"); why.append(f"{name} age {age/60:.1f} min")
        else:
            out.append("PASS")
    r = combine(out)
    m = {"market_open": True, "max_age_min": round(max(ages) / 60, 2) if ages else None}
    return res(rule, r, m, thr, "; ".join(why) or "all quotes <= 15 min old, market open")


def r02(rule, c: Ctx):
    lim = D(c.cfg["max_spread_pct_of_mid"])
    per, out = [], []
    for l in c.inp["legs"]:
        mid, why = leg_mid(l)
        if mid is None:
            per.append({"leg_id": l["leg_id"], "result": "UNKNOWN", "measured": None, "reason": why}); out.append("UNKNOWN"); continue
        w = (D(l["ask"]["value"]) - D(l["bid"]["value"])) / mid
        r = "PASS" if w <= lim else "VETO"
        per.append({"leg_id": l["leg_id"], "result": r, "measured": float(round(w, 4))}); out.append(r)
    r = combine(out)
    reason = ", ".join(f"{p['leg_id']} {p['measured'] if p['measured'] is not None else 'n/a'} {p['result']}" for p in per)
    return res(rule, r, {p["leg_id"]: p["measured"] for p in per}, {"max_spread_pct_of_mid": float(lim)}, reason, per)


def r03(rule, c: Ctx):
    mn = c.cfg["min_open_interest"]
    per, out = [], []
    for l in c.inp["legs"]:
        f = l.get("open_interest")
        if not usable(f):
            per.append({"leg_id": l["leg_id"], "result": "UNKNOWN", "measured": None}); out.append("UNKNOWN"); continue
        r = "PASS" if f["value"] >= mn else "VETO"
        per.append({"leg_id": l["leg_id"], "result": r, "measured": f["value"]}); out.append(r)
    reason = ", ".join(f"{p['leg_id']} OI {p['measured']} {p['result']}" for p in per)
    return res(rule, combine(out), {p["leg_id"]: p["measured"] for p in per}, {"min_open_interest": mn}, reason, per)


def r04(rule, c: Ctx):
    lim = c.cfg.get("per_trade_max_loss_usd")
    thr = {"per_trade_max_loss_usd": lim}
    ml = payoff_max_loss(c.inp, c.cfg)
    if ml.get("error") or lim is None:
        return res(rule, "UNKNOWN", None, thr, ml.get("error") or "per-trade limit not configured")
    if ml["unlimited"]:
        return res(rule, "VETO", {"unlimited": True, "max_loss_usd": None}, thr, "unlimited loss (net short calls)")
    r = "PASS" if D(ml["max_loss_usd"]) <= D(lim) else "VETO"
    return res(rule, r, {"unlimited": False, "max_loss_usd": ml["max_loss_usd"]}, thr,
               f"max loss ${ml['max_loss_usd']:.2f} at limit vs ${lim} (placeholder pending Eugene)" if "per_trade_max_loss_usd" in c.cfg.get("placeholders", []) else f"max loss ${ml['max_loss_usd']:.2f} vs ${lim}")


def r05(rule, c: Ctx):
    lp = c.inp["ticket"]["limit_price"]
    basis = c.cfg["net_price_basis"]
    thr = {"limit": lp["amount"], "price_type": lp["price_type"], "basis": basis}
    net = D(0)
    for l in c.inp["legs"]:
        mid, why = leg_mid(l)
        if mid is None:
            return res(rule, "UNKNOWN", None, thr, f"{l['leg_id']}: {why}")
        if basis == "natural":
            px = D(l["ask"]["value"]) if l["side"] == "buy" else D(l["bid"]["value"])
        else:
            px = mid
        net += (1 if l["side"] == "buy" else -1) * D(l["ratio"]) * px
    limit = D(lp["amount"])
    if lp["price_type"] == "debit":
        r = "PASS" if net <= limit else "VETO"
        reason = f"net debit {net} vs limit {limit}"
    else:
        r = "PASS" if -net >= limit else "VETO"
        reason = f"net credit {-net} vs limit {limit}"
    return res(rule, r, {"net": float(net)}, thr, reason)


def r06(rule, c: Ctx):
    if not c.inp["legs"]:
        return res(rule, "UNKNOWN", None, {"min_dte": c.cfg["min_dte"]}, "no legs")
    today = c.t.astimezone(NY).date()
    dte = min((date.fromisoformat(l["expiry"]) - today).days for l in c.inp["legs"])
    r = "PASS" if dte >= c.cfg["min_dte"] else "VETO"
    return res(rule, r, {"dte": dte}, {"min_dte": c.cfg["min_dte"]}, f"{dte} DTE (NY date {today})")


def r07(rule, c: Ctx):
    u = c.inp["ticket"]["underlying"]
    intended = set(c.inp["ticket"].get("intended_event_ids", []))
    out, why, hits = [], [], []
    ok, msg = c.coverage_ok("earnings", u)
    if not ok:
        out.append("UNKNOWN"); why.append(msg)
    for ev in c.inp.get("events", []):
        if ev["kind"] != "earnings" or ev.get("underlying") != u:
            continue
        iv = c.event_interval(ev)
        if iv is None:
            out.append("UNKNOWN"); why.append(f"{ev['event_id']} date unusable"); continue
        if c.in_window(*iv):
            hits.append(ev["event_id"])
            if ev["event_id"] in intended:
                out.append("FLAG"); why.append(f"{ev['event_id']} in window (intended)")
            else:
                out.append("VETO"); why.append(f"{ev['event_id']} in window, not intended")
    out.append("PASS")
    return res(rule, combine(out), {"events_in_window": hits, "window": [c.w_start.isoformat(), c.w_end.isoformat()]},
               {"intended_event_ids": sorted(intended)}, "; ".join(why) or "no earnings in hold window")


def r08(rule, c: Ctx):
    kinds = c.cfg["macro_event_kinds"]
    out, why, hits = ["PASS"], [], []
    for k in kinds:
        ok, msg = c.coverage_ok(k, None)
        if not ok:
            out.append("UNKNOWN"); why.append(msg)
    for ev in c.inp.get("events", []):
        if ev["kind"] not in kinds:
            continue
        iv = c.event_interval(ev)
        if iv is None:
            out.append("UNKNOWN"); why.append(f"{ev['event_id']} date unusable"); continue
        if c.in_window(*iv):
            out.append("FLAG"); hits.append(ev["event_id"]); why.append(f"{ev['kind']} {ev['event_id']} in window")
    return res(rule, combine(out), {"events_in_window": hits}, {"macro_event_kinds": kinds}, "; ".join(why) or "no macro events in window")


def r09(rule, c: Ctx):
    u = c.inp["ticket"]["underlying"]
    shorts = [l for l in c.inp["legs"] if l["side"] == "sell"]
    thr = {"min_put_extrinsic": float(D(c.cfg["min_put_extrinsic"]))}
    if not shorts:
        return res(rule, "PASS", None, thr, "no short legs", [])
    S_f = c.inp["underlying"].get("last")
    S = D(S_f["value"]) if usable(S_f) else None
    per, out = [], []
    exdivs, exdiv_unknown = [], []
    if any(l["type"] == "call" for l in shorts):
        ok, msg = c.coverage_ok("ex_dividend", u)
        if not ok:
            exdiv_unknown.append(msg)
        for ev in c.inp.get("events", []):
            if ev["kind"] == "ex_dividend" and ev.get("underlying") == u:
                inst = c.exdiv_exposure(ev)
                if inst is None or not usable(ev.get("dividend_amount")):
                    exdiv_unknown.append(f"{ev['event_id']} date/dividend unusable"); continue
                if c.w_start <= inst <= c.w_end:
                    exdivs.append((ev, D(ev["dividend_amount"]["value"])))
    for l in shorts:
        K = D(l["strike"]); lid = l["leg_id"]
        mid, _ = leg_mid(l)
        if l["type"] == "call":
            r_items = ["UNKNOWN"] if exdiv_unknown else []
            m = {"exdiv_in_window": [e["event_id"] for e, _ in exdivs]}
            for ev, div in exdivs:
                if S is None or mid is None:
                    r_items.append("UNKNOWN"); continue
                intr = max(D(0), S - K); ext = mid - intr
                m.update({"itm": S > K, "extrinsic": float(ext), "dividend": float(div)})
                r_items.append("VETO" if (S > K or ext < div) else "FLAG")
            r = combine(r_items) if r_items else "PASS"
            per.append({"leg_id": lid, "result": r, "measured": m,
                        "reason": "; ".join(exdiv_unknown) or ("ex-div in window" if exdivs else "no ex-div in window")})
        else:
            if S is None or mid is None:
                per.append({"leg_id": lid, "result": "UNKNOWN", "measured": None, "reason": "spot or quote unusable"}); out.append("UNKNOWN"); continue
            intr = max(D(0), K - S); ext = mid - intr
            r = "FLAG" if (K > S and ext < D(c.cfg["min_put_extrinsic"])) else "PASS"
            per.append({"leg_id": lid, "result": r, "measured": {"itm": K > S, "extrinsic": float(ext)}})
        out.append(per[-1]["result"])
    r = combine(out)
    return res(rule, r, {p["leg_id"]: p["measured"] for p in per}, thr,
               "; ".join(f"{p['leg_id']} {p['result']}" + (f" ({p['reason']})" if p.get("reason") else "") for p in per), per)


def r10(rule, c: Ctx):
    iv = c.inp.get("iv") or {}
    cur, hist = iv.get("iv30_current"), iv.get("iv30_history")
    thr = {"iv_rank_high": float(D(c.cfg["iv_rank_high"])), "iv_rank_low": float(D(c.cfg["iv_rank_low"])), "min_obs": c.cfg["iv_min_history_obs"]}
    if not (usable(cur) and usable(hist)):
        return res(rule, "UNKNOWN", None, thr, "IV30 current or history unavailable")
    h = [D(x) for x in hist["value"]]
    if len(h) < c.cfg["iv_min_history_obs"]:
        return res(rule, "UNKNOWN", {"obs": len(h)}, thr, f"only {len(h)} history observations")
    lo, hi, cv = min(h), max(h), D(cur["value"])
    rank = D("0.5") if hi == lo else (cv - lo) / (hi - lo)
    pt = c.inp["ticket"]["limit_price"]["price_type"]
    if pt == "debit" and rank > D(c.cfg["iv_rank_high"]):
        r = "FLAG"
    elif pt == "credit" and rank < D(c.cfg["iv_rank_low"]):
        r = "FLAG"
    else:
        r = "PASS"
    return res(rule, r, {"iv_rank": float(round(rank, 4)), "iv30": float(cv), "min": float(lo), "max": float(hi)}, thr, f"IV rank {float(rank):.2f} ({pt})")


def r11(rule, c: Ctx):
    cfg = c.cfg
    thr = {k: cfg.get(k) for k in ("max_underlying_risk_usd", "max_sector_risk_usd", "max_total_open_risk_usd", "max_open_positions")}
    op = c.inp.get("open_positions")
    if not usable(op):
        return res(rule, "UNKNOWN", None, thr, "open positions unknown")
    ml = payoff_max_loss(c.inp, cfg)
    if ml.get("error") or ml.get("unlimited"):
        return res(rule, "FLAG", {"ticket_max_loss": None}, thr, "ticket max loss unlimited/unknown")
    u = c.inp["ticket"]["underlying"]
    t = D(ml["max_loss_usd"])
    same = sum((D(p["max_loss_usd"]) for p in op["value"] if p["underlying"] == u), D(0)) + t
    total = sum((D(p["max_loss_usd"]) for p in op["value"]), D(0)) + t
    n = len(op["value"]) + 1
    measured = {"same_underlying_usd": float(same), "total_usd": float(total), "count": n}
    why = []
    flag = same > D(thr["max_underlying_risk_usd"]) or total > D(thr["max_total_open_risk_usd"]) or n > thr["max_open_positions"]
    # sector cap (v1.1): only when max_sector_risk_usd is configured (null = not checked)
    if thr["max_sector_risk_usd"] is not None:
        smap = cfg.get("sector_map") or {}
        sec = smap.get(u)
        unmapped = sorted({p["underlying"] for p in op["value"] if p.get("sector", smap.get(p["underlying"])) is None} | ({u} if sec is None else set()))
        if unmapped:
            return res(rule, "UNKNOWN", {**measured, "sector": sec, "same_sector_usd": None}, thr, f"no sector mapping for {', '.join(unmapped)}")
        same_sec = sum((D(p["max_loss_usd"]) for p in op["value"] if p.get("sector", smap.get(p["underlying"])) == sec), D(0)) + t
        measured.update(sector=sec, same_sector_usd=float(same_sec))
        flag = flag or same_sec > D(thr["max_sector_risk_usd"])
        why.append(f"sector {sec} ${same_sec}")
    tag = " (placeholder limits pending Eugene)" if "max_underlying_risk_usd" in cfg.get("placeholders", []) else ""
    return res(rule, "FLAG" if flag else "PASS", measured, thr,
               f"same-underlying ${same}, {', '.join(why) + ', ' if why else ''}total ${total}, positions {n}{tag}")


VERTICALS = {  # strategy -> (option type, price_type, which strike is bought: low|high)
    "bull_call_debit_spread": ("call", "debit", "low"), "bear_put_debit_spread": ("put", "debit", "high"),
    "bull_put_credit_spread": ("put", "credit", "low"), "bear_call_credit_spread": ("call", "credit", "high"),
}


def _shape_errors(strategy, legs, price_type):
    """-> list of reasons the legs do not form the declared strategy ([] = matches)."""
    if strategy in VERTICALS:
        typ, pt, bought = VERTICALS[strategy]
        if len(legs) != 2:
            return [f"{strategy} needs 2 legs, got {len(legs)}"]
        a, b = legs
        errs = []
        if {a["type"], b["type"]} != {typ}: errs.append(f"{strategy} legs must both be {typ}s")
        if a["expiry"] != b["expiry"]: errs.append("vertical legs must share one expiry")
        if a["ratio"] != b["ratio"]: errs.append("vertical legs must have equal ratio")
        if {a["side"], b["side"]} != {"buy", "sell"}: errs.append("vertical needs one buy and one sell leg")
        if a["strike"] == b["strike"]: errs.append("vertical strikes must differ")
        if not errs:
            lo, hi = sorted(legs, key=lambda l: l["strike"])
            if (lo if bought == "low" else hi)["side"] != "buy":
                errs.append(f"{strategy} must buy the {bought}er strike")
        if price_type != pt: errs.append(f"{strategy} must be priced as {pt}, ticket says {price_type}")
        return errs
    if strategy in ("long_call", "long_put"):
        typ = strategy.split("_")[1]
        ok = len(legs) == 1 and legs[0]["type"] == typ and legs[0]["side"] == "buy" and price_type == "debit"
        return [] if ok else [f"{strategy} must be a single bought {typ} priced as debit"]
    if strategy == "iron_condor":
        puts = [l for l in legs if l["type"] == "put"]; calls = [l for l in legs if l["type"] == "call"]
        if len(legs) != 4 or len(puts) != 2 or len(calls) != 2:
            return ["iron_condor needs 2 puts + 2 calls"]
        errs = _shape_errors("bull_put_credit_spread", puts, "credit") + _shape_errors("bear_call_credit_spread", calls, "credit")
        if len({l["expiry"] for l in legs}) > 1: errs.append("iron_condor legs must share one expiry")
        if price_type != "credit": errs.append("iron_condor must be priced as credit")
        return errs
    return [f"no shape definition for strategy {strategy!r}"]


def r12(rule, c: Ctx):
    tk, legs = c.inp["ticket"], c.inp["legs"]
    allowed = list(c.cfg.get("allowed_structures") or [])
    thr = {"allowed_structures": allowed}
    strategy = tk.get("strategy")
    if not legs or not strategy:
        return res(rule, "UNKNOWN", None, thr, "no legs" if not legs else "ticket strategy missing")
    sign = lambda l: 1 if l["side"] == "buy" else -1
    net = {typ: sum(sign(l) * l["ratio"] for l in legs if l["type"] == typ) for typ in ("call", "put")}
    naked = [typ for typ, v in net.items() if v < 0]
    measured = {"strategy": strategy, "naked_short": naked, "defined_risk": not naked}
    if naked:
        return res(rule, "VETO", measured, thr, f"naked short {'/'.join(naked)} (more contracts sold than bought): not defined-risk")
    if strategy not in allowed:
        return res(rule, "VETO", measured, thr, f"structure {strategy} not in allowed_structures")
    errs = _shape_errors(strategy, legs, (tk.get("limit_price") or {}).get("price_type"))
    if errs:
        return res(rule, "VETO", measured, thr, "; ".join(errs))
    return res(rule, "PASS", measured, thr, f"{strategy}: defined-risk, legs match")


def r13(rule, c: Ctx):
    lo, hi = c.cfg.get("hold_min_days"), c.cfg.get("hold_max_days")
    thr = {"hold_min_days": lo, "hold_max_days": hi}
    legs, tk = c.inp["legs"], c.inp["ticket"]
    if not legs or lo is None:
        return res(rule, "UNKNOWN", None, thr, "no legs" if not legs else "hold_min_days not configured")
    today = c.t.astimezone(NY).date()
    start = max(c.t, ts(tk["hold_window_start"])).astimezone(NY).date()
    stop = ts(tk["hold_window_end"])
    hold = (stop.astimezone(NY).date() - start).days
    dte = min((date.fromisoformat(l["expiry"]) - today).days for l in legs)
    first_close = min(c.cal.close_dt(date.fromisoformat(l["expiry"])) for l in legs)
    m = {"hold_days": hold, "dte": dte}
    why = []
    if stop > first_close: why.append(f"time stop {stop.astimezone(NY).isoformat()} after expiry close {first_close.isoformat()}")
    for name, v in (("time stop", hold), ("DTE", dte)):
        if v < lo or (hi is not None and v > hi):
            why.append(f"{name} {v} d outside {lo}-{hi if hi is not None else 'inf'} d")
    return res(rule, "VETO" if why else "PASS", m, thr, "; ".join(why) or f"time stop in {hold} d, DTE {dte} (NY date {today})")


RULE_FUNCS = {"R01_QUOTE_FRESHNESS": r01, "R02_BID_ASK_WIDTH": r02, "R03_OPEN_INTEREST": r03, "R04_MAX_LOSS": r04,
              "R05_NET_PRICE_VS_LIMIT": r05, "R06_MIN_DTE": r06, "R07_EARNINGS_IN_WINDOW": r07, "R08_MACRO_EVENTS": r08,
              "R09_EARLY_ASSIGNMENT": r09, "R10_IV_VS_HISTORY": r10, "R11_CONCENTRATION": r11,
              "R12_DEFINED_RISK_STRUCTURE": r12, "R13_HOLD_PERIOD": r13}


def overall(results) -> dict:
    blocking = [r["rule_id"] for r in results if r["result"] == "VETO" or (r["result"] == "UNKNOWN" and r["severity"] == "hard")]
    flags = [r["rule_id"] for r in results if r["result"] == "FLAG" or (r["result"] == "UNKNOWN" and r["severity"] == "soft")]
    verdict = "VETO" if blocking else ("FLAG" if flags else "PASS")
    return {"verdict": verdict, "blocking_rule_ids": blocking, "flag_rule_ids": flags, "approval_required": True}


def profile_overrides(rules: dict, profile: str | None) -> dict:
    """config overrides of a named profile in risk_rules.yaml `profiles` (None -> {} = documented defaults)."""
    if profile is None:
        return {}
    return dict(rules["profiles"][profile]["config_overrides"])


def evaluate(inp: dict, rules: dict | None = None, config_overrides: dict | None = None, profile: str | None = None) -> dict:
    """cfg = defaults <- profile overrides <- per-call overrides."""
    rules = rules or load_rules()
    cfg = {**rules["config"], **profile_overrides(rules, profile), **(config_overrides or {})}
    c = Ctx(inp, cfg)
    results = [RULE_FUNCS[r["id"]](r, c) for r in rules["rules"]]
    return {"results": results, "overall": overall(results), "market_open_at_evaluation": c.cal.is_open(c.t)}


# ------------------------------------------------------- session adapter
def tspair(dt: datetime) -> dict:
    return {"ny": dt.astimezone(NY).isoformat(), "berlin": dt.astimezone(BERLIN).isoformat()}


def inputs_from_session(sess: dict, ticket_id: str, run_id: str, evaluated_at: str) -> dict:
    """Build a review input from a session record, with point-in-time visibility:
    a Field is visible only if its source.fetched_at <= evaluated_at."""
    t = ts(evaluated_at)
    fetched = {s["id"]: ts(s["fetched_at"]) for s in sess["sources"]}
    MISSING = {"value": None, "status": "missing", "as_of": None}

    def vis(f):
        if not isinstance(f, dict):
            return dict(MISSING)
        sid = f.get("source_id")
        if sid and sid in fetched and fetched[sid] > t:
            return dict(MISSING)
        return {"value": f["value"], "status": f["status"], "as_of": f["as_of"]}

    tk = next(x for x in sess["tickets"] if x["ticket_id"] == ticket_id)
    cand = next(x for x in sess["candidates"] if x["candidate_id"] == tk["candidate_id"])
    snap = next(s for s in cand["snapshots"] if s["run_id"] == run_id)
    contracts = {k["contract_id"]: k for sl in snap["option_chain_slices"] for k in sl["contracts"]}
    legs = []
    for l in tk["legs"]:
        k = contracts.get(l["contract_id"], {})
        legs.append({**{x: l[x] for x in ("leg_id", "type", "side", "strike", "expiry", "ratio")},
                     "bid": vis(k.get("bid")), "ask": vis(k.get("ask")), "open_interest": vis(k.get("open_interest"))})
    ev_cal = sess["event_calendar"]
    events = []
    for e in ev_cal["events"]:
        ev = {"event_id": e["event_id"], "kind": e["kind"], "underlying": e["underlying"], "timing": e["timing"],
              "scheduled_at": vis(e["scheduled_at"])}
        if "dividend_amount" in e:
            ev["dividend_amount"] = vis(e["dividend_amount"])
        events.append(ev)
    coverage = [{"kind": c["kind"], "underlying": c["underlying"], "checked_through": vis(c["checked_through"])} for c in ev_cal["coverage"]]
    iv = snap.get("iv") or {}
    tickets = {x["ticket_id"]: x for x in sess["tickets"]}
    open_pos = []
    for p in sess["paper_positions"]:
        if p["status"] == "open" and p["ticket_id"] != ticket_id:
            ptk = tickets[p["ticket_id"]]
            open_pos.append({"underlying": ptk["underlying"], "max_loss_usd": ptk["max_loss"]["amount_usd"]})
    return {
        "evaluated_at": evaluated_at,
        "underlying": {"symbol": cand["underlying"], "last": vis(snap["quote"]["last"])},
        "ticket": {"underlying": tk["underlying"], "strategy": tk["strategy"], "quantity": tk["quantity"], "limit_price": tk["limit_price"],
                   "hold_window_start": tk["hold_window"]["start"]["ny"], "hold_window_end": tk["hold_window"]["end"]["ny"],
                   "intended_event_ids": tk["intended_event_ids"]},
        "legs": legs, "events": events, "coverage": coverage,
        "iv": {"iv30_current": vis(iv.get("iv30_current")), "iv30_history": vis(iv.get("iv30_history"))},
        "open_positions": {"value": open_pos, "status": "ok", "as_of": evaluated_at},
    }


# ------------------------------------------------------- test-case support
def _path_parts(p: str):
    return [int(x) if x.isdigit() else x for x in p.split(".")]


def _get_parent(obj, parts):
    for k in parts[:-1]:
        obj = obj[k]
    return obj


def apply_case(fixture: dict, case: dict) -> dict:
    inp = copy.deepcopy(fixture)
    for p, v in (case.get("set") or {}).items():
        parts = _path_parts(p); _get_parent(inp, parts)[parts[-1]] = copy.deepcopy(v)
    for p, items in (case.get("append") or {}).items():
        parts = _path_parts(p); _get_parent(inp, parts)[parts[-1]].extend(copy.deepcopy(items))
    for p in sorted(case.get("remove") or [], reverse=True):
        parts = _path_parts(p); del _get_parent(inp, parts)[parts[-1]]
    return inp
