"""Pure construction of HYPOTHETICAL defined-risk vertical debit spreads as schema-v1 tickets.

Owner: Doodle (strategy numbers) / Poodle (code). Works on plain schema dicts; no I/O.
Every ticket carries the fixed HYPOTHETICAL label; nothing here can become an order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from ..records.compat import V11 as RECORD_SCHEMA_VERSION
from .verticals import validate_vertical
from .ticks import TickRule
from ..models.contracts import HYPOTHETICAL_LABEL, PlannerParams
from ..records.fields import NY, ny_at, slug, tspair

STATUS_RANK = {"ok": 0, "stale": 1, "conflicting": 2, "missing": 3}
BULL, BEAR = "bull_call_debit_spread", "bear_put_debit_spread"


def r2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _round_strike(spot: float, step: float, up: bool) -> float:
    return float((math.ceil(spot / step) if up else math.floor(spot / step)) * step)


def worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: STATUS_RANK.get(s, 3))


@dataclass
class Idea:
    candidate_id: str
    underlying: str
    strategy: str
    description: str
    ticket: Optional[dict] = None
    reason: str = ""


def describe(sym: str, expiry: date, strategy: str, k1: float, k2: float) -> str:
    kind = "call spread" if strategy == BULL else "put spread"
    return f"{sym} {expiry:%b%d} {k1:g}/{k2:g} {kind}"


def pick_expiry(slices: list[dict], today: date, dte_min: int, dte_max: int) -> Optional[dict]:
    inside = [s for s in sorted(slices, key=lambda s: s["expiry"])
              if dte_min <= (date.fromisoformat(s["expiry"]) - today).days <= dte_max]
    return inside[0] if inside else None


def _find(sl: dict, strike: float, typ: str) -> Optional[dict]:
    for c in sl["contracts"]:
        if c["type"] == typ and abs(c["strike"] - strike) < 1e-9:
            return c
    return None


def build_vertical(cand: dict, strategy: str, p: PlannerParams, as_of: datetime, run_id: str,
                   thesis: str = "") -> Idea:
    sym, cid = cand["underlying"], cand["candidate_id"]
    snap = cand["snapshots"][-1]
    q = snap["quote"]
    spot, prev = q["last"]["value"], q["prev_close"]["value"]
    idea = Idea(cid, sym, strategy, f"{sym} {'bull call' if strategy == BULL else 'bear put'} debit spread")
    if spot is None:
        idea.reason = f"no underlying price ({q['last']['status']})"
        return idea
    today = as_of.astimezone(NY).date()
    sl = pick_expiry(snap["option_chain_slices"], today, p.preferred_dte_min, p.preferred_dte_max)
    if sl is None:
        idea.reason = f"no expiry with {p.preferred_dte_min}-{p.preferred_dte_max} DTE in chain"
        return idea
    expiry = date.fromisoformat(sl["expiry"])
    if strategy == BULL:
        typ, long_k = "call", _round_strike(spot, p.strike_step, up=True)
        short_k = long_k + p.spread_width
    else:
        typ, long_k = "put", _round_strike(spot, p.strike_step, up=False)
        short_k = long_k - p.spread_width
    idea.description = describe(sym, expiry, strategy, long_k, short_k)
    lc, sc = _find(sl, long_k, typ), _find(sl, short_k, typ)
    if lc is None or sc is None:
        idea.reason = f"strikes {long_k:g}/{short_k:g} {typ} not in chain for {expiry}"
        return idea
    if lc["mid"]["value"] is None or sc["mid"]["value"] is None:
        idea.reason = "missing bid/ask on a leg"
        return idea
    mid = round(lc["mid"]["value"] - sc["mid"]["value"], 6)
    natural = round(lc["ask"]["value"] - sc["bid"]["value"], 6)
    if mid <= 0:
        idea.reason = f"non-positive reference debit {mid:.3f}"
        return idea
    width = abs(long_k - short_k)
    ticks = TickRule.from_spec(p.tick_rules, p.penny_pilot, p.tick_threshold)
    side = "debit"                     # the planner only builds debit verticals; credit rounding is in ticks.py
    lim_r = ticks.round_for(sym, mid * (1 + p.entry_slippage_pct / 100), side, "limit")     # debit cap: never pay more
    limit = lim_r.value
    if limit <= 0:
        idea.reason = f"limit {lim_r.raw:.4f} rounds to zero on a {lim_r.tick:g} tick"
        return idea
    qty = int(p.max_risk_per_trade_usd // (limit * 100))
    if qty < 1:
        idea.reason = f"1 spread at {limit:.2f} risks ${limit * 100:.0f} > max risk ${p.max_risk_per_trade_usd:g}"
        return idea
    pstatus = worst(lc["bid"]["status"], lc["ask"]["status"], sc["bid"]["status"], sc["ask"]["status"])
    time_stop = expiry - timedelta(days=p.time_stop_days_before_expiry)
    stop_r = ticks.round_for(sym, mid * p.stop_fraction_of_debit, side, "stop")          # exit no later
    target_r = ticks.round_for(sym, min(mid * p.target_multiple_of_debit, width * 0.9), side, "target")  # profit no later
    stop_v, target_v = stop_r.value, target_r.value
    raw_of = lambda r: {"raw_value": r.raw, "tick_size": r.tick, "rounding": r.direction}  # noqa: E731
    tick_note = (f"tick rounding [{ticks.source}] ({'penny-pilot' if sym.upper() in ticks.penny_pilot else 'standard'} {sym}): "
                 f"limit {lim_r.text()}; stop {stop_r.text()}; target {target_r.text()}")
    ref = prev if prev is not None else spot
    bull = strategy == BULL
    u_stop = _round_strike(ref, p.strike_step, up=not bull)
    start = max(as_of.astimezone(NY), ny_at(today, p.entry_not_before))
    k_tag = f"{long_k:g}-{short_k:g}".replace(".", "p")
    tid = f"tkt-{slug(sym)}-{'bcs' if bull else 'bps'}-{k_tag}"
    be = (lambda px: r2(long_k + px) if bull else r2(long_k - px))
    ccy = lambda x: f"{x:.2f}"
    cmp_ = "gt" if bull else "lt"
    side_txt = ">" if bull else "<"
    ticket: dict[str, Any] = {
        "ticket_id": tid, "candidate_id": cid, "label": HYPOTHETICAL_LABEL, "created": tspair(as_of),
        "underlying": sym, "strategy": strategy, "direction": "bullish" if bull else "bearish",
        "thesis": thesis or idea.description,
        "legs": [
            {"leg_id": "leg-1", "contract_id": lc["contract_id"], "type": typ, "side": "buy", "strike": long_k,
             "expiry": sl["expiry"], "ratio": 1, "multiplier": 100},
            {"leg_id": "leg-2", "contract_id": sc["contract_id"], "type": typ, "side": "sell", "strike": short_k,
             "expiry": sl["expiry"], "ratio": 1, "multiplier": 100}],
        "quantity": qty,
        "limit_price": {"amount": limit, "raw_amount": lim_r.raw, "tick_size": lim_r.tick, "rounding": lim_r.direction,
                        "price_type": "debit",
                        "valid_only_if": (f"re-quoted live after {p.entry_not_before} ET with each leg spread <= "
                                          f"{p.max_leg_spread_pct:g}% of mid; reference mid {mid:g}, cap = mid x "
                                          f"{1 + p.entry_slippage_pct / 100:g}")},
        "pricing": {"run_id": run_id, "reference_mid": mid, "natural": natural, "status": pstatus,
                    "note": "; ".join([*(["UNPRICEABLE premarket: prior-session option quotes"]
                                          if pstatus != "ok" else []), tick_note])},
        "risk_basis": p.risk_basis or f"Per-trade max loss ${p.max_risk_per_trade_usd:g} is a PLACEHOLDER pending Eugene",
        "hold_window": {"start": tspair(start), "end": tspair(ny_at(time_stop, "16:00"))},
        "entry_rules": [{"rule_id": "entry-1", "combine": "all",
                         "text": (f"After {p.entry_not_before} ET, only if {sym} {side_txt} {ref:g} (prev close), "
                                  f"live net mid debit <= {ccy(limit)} and each leg spread <= {p.max_leg_spread_pct:g}% of mid"),
                         "conditions": [{"metric": "time_ny", "op": "gte", "value": p.entry_not_before},
                                        {"metric": "underlying_last", "op": cmp_, "value": ref},
                                        {"metric": "net_debit_mid", "op": "lte", "value": limit, **raw_of(lim_r)},
                                        {"metric": "leg_spread_pct_of_mid", "op": "lte",
                                         "value": p.max_leg_spread_pct, "unit": "pct"}]}],
        "exit_rules": [{"rule_id": "exit-time", "text": f"Time stop: close by {time_stop.isoformat()}",
                        "conditions": [{"metric": "date_ny", "op": "gte", "value": time_stop.isoformat()}]}],
        "stop_rules": [{"rule_id": "stop-1", "combine": "any",
                        "text": (f"Exit if {sym} closes {'below' if bull else 'above'} {u_stop:g} or spread value <= "
                                 f"{ccy(stop_v)} (~{p.stop_fraction_of_debit:.0%} of debit)"),
                        "conditions": [{"metric": "underlying_close", "op": "lt" if bull else "gt", "value": u_stop},
                                       {"metric": "position_value", "op": "lte", "value": stop_v, **raw_of(stop_r)}]}],
        "target_rules": [{"rule_id": "target-1", "combine": "any",
                          "text": f"Close at spread value {ccy(target_v)} or {sym} {'>=' if bull else '<='} {short_k:g}",
                          "conditions": [{"metric": "position_value", "op": "gte", "value": target_v, **raw_of(target_r)},
                                         {"metric": "underlying_last", "op": "gte" if bull else "lte", "value": short_k}]}],
        "max_loss": {"amount_usd": r2(qty * limit * 100), "unlimited": False, "at_price": limit,
                     "basis": f"{qty} x {ccy(limit)} x 100 at limit; ${qty * mid * 100:.2f} at ref mid {mid:g}"},
        "max_profit": {"amount_usd": r2(qty * (width - limit) * 100), "unlimited": False, "at_price": limit,
                       "basis": f"({width:g} width - {ccy(limit)}) x 100 x {qty}"},
        "breakevens": [{"underlying_price": be(limit), "at_price": limit},
                       {"underlying_price": round(long_k + mid if bull else long_k - mid, 6), "at_price": mid}],
        "intended_event_ids": [],
        "dm_alternatives": [],          # filled by the planner once all ideas are known
    }
    # INV-14 (+ INV-15 once records are 1.1.2), on every planner ticket, for the schema we write: fail closed
    errs = validate_vertical(ticket, RECORD_SCHEMA_VERSION)
    if errs:
        idea.reason = "INV-14: " + "; ".join(errs)
        return idea
    idea.ticket = ticket
    return idea
