"""Vertical spread economics from legs (INV-14, R04). Exact Decimal; prices via repr() so float noise stays visible.

Kinds (2 legs, same type, same expiry, ratio 1:1, one buy + one sell):
  bull_call_debit_spread  buy lower call,  sell higher call   debit
  bear_put_debit_spread   buy higher put,  sell lower put     debit
  bull_put_credit_spread  sell higher put, buy lower put      credit
  bear_call_credit_spread sell lower call, buy higher call    credit
All money figures use the LIMIT price (worst acceptable: max debit / min credit).
"""
from __future__ import annotations

from decimal import Decimal

VERTICALS = {
    # strategy: (option type, price_type, long leg is the higher strike?)
    "bull_call_debit_spread": ("call", "debit", False),
    "bear_put_debit_spread": ("put", "debit", True),
    "bull_put_credit_spread": ("put", "credit", False),
    "bear_call_credit_spread": ("call", "credit", True),
}


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(repr(x)) if isinstance(x, float) else Decimal(str(x))


def is_vertical(strategy: str) -> bool:
    return strategy in VERTICALS


def compute(strategy: str, legs: list, quantity: int, limit_price: dict, multiplier=100) -> dict:
    """-> {"error": str} or dict(kind, price_type, long_strike, short_strike, width, price, qty, mult,
    max_loss, max_profit, breakeven_at(price) callable)."""
    if strategy not in VERTICALS:
        return {"error": f"{strategy} is not a vertical"}
    otype, ptype, long_high = VERTICALS[strategy]
    if len(legs) != 2:
        return {"error": f"{strategy} needs exactly 2 legs, has {len(legs)}"}
    if {l["type"] for l in legs} != {otype}:
        return {"error": f"{strategy} legs must both be {otype}s"}
    if len({l["expiry"] for l in legs}) != 1:
        return {"error": f"{strategy} legs must share one expiry"}
    if any(l.get("ratio", 1) != 1 for l in legs):
        return {"error": f"{strategy} legs must be ratio 1:1"}
    buys = [l for l in legs if l["side"] == "buy"]; sells = [l for l in legs if l["side"] == "sell"]
    if len(buys) != 1 or len(sells) != 1:
        return {"error": f"{strategy} needs one buy and one sell leg"}
    lk, sk = D(buys[0]["strike"]), D(sells[0]["strike"])
    if lk == sk or (lk > sk) != long_high:
        return {"error": f"{strategy} strikes: long {lk} / short {sk} do not form this vertical"}
    if limit_price.get("price_type") != ptype:
        return {"error": f"{strategy} limit price_type must be {ptype}, is {limit_price.get('price_type')}"}
    width, price = abs(lk - sk), D(limit_price["amount"])
    qty, mult = D(quantity), D(multiplier)
    if ptype == "debit":
        ml, mp = price * mult * qty, (width - price) * mult * qty
        be = (lambda p: lk + D(p)) if otype == "call" else (lambda p: lk - D(p))
    else:
        ml, mp = (width - price) * mult * qty, price * mult * qty
        be = (lambda p: sk - D(p)) if otype == "put" else (lambda p: sk + D(p))
    return {"kind": strategy, "otype": otype, "price_type": ptype, "long_strike": lk, "short_strike": sk, "width": width,
            "price": price, "qty": qty, "mult": mult, "max_loss": ml, "max_profit": mp, "breakeven_at": be}
