"""INV-14 vertical-spread validation (spec 1.1.1, pinned 2026-10-29 21:17 CET) + INV-15 (spec 1.1.2, pinned 2026-10-29 21:32 CET).

Runs on every planner ticket (builder.build_vertical -> validate_vertical); a ticket with any error is not emitted.
All values are derived from the LEGS (strikes, width) + quantity + limit price (the worst acceptable price:
max debit / min credit). Exact Decimal; dollar amounts compared exactly to $0.01.

  CREDIT (bull put / bear call)              DEBIT (bull call / bear put)
  0 < min_credit < width                     0 < max_debit < width
  min_credit < stop_debit < width            0 < stop < max_debit
  0 <= target_buyback < min_credit           max_debit < target <= width
  max_loss   = (width - credit) x 100 x qty  max_loss   = debit x 100 x qty
  max_profit = credit x 100 x qty            max_profit = (width - debit) x 100 x qty
  BE: put short - credit / call short + cr.  BE: call long + debit / put long - debit
  loss at stop = (stop - credit) x100 x qty <= max_loss
  rounding: limit UP, stop DOWN, target UP   rounding: limit DOWN, stop UP, target DOWN   (each on tick)

INV-15 (spec 1.1.2 records ONLY; 1.1.0/1.1.1 unaffected; 1.1.2 keeps every INV-14 check): the op of the SAME
single position_value stop / target condition INV-14 reads. Strict: lt/gt fail. A side INV-14 already flagged
as missing or duplicated is skipped.
  credit: stop gte (debit to close rises), target lte (buy-back falls)
  debit:  stop lte (spread value falls),   target gte (spread value rises)

Structure (as the spec's verticals.py / INV-14): 2 legs, same option type and expiry, ratio 1:1, multiplier 100,
exactly one buy and one sell, strikes ordered for the declared structure, limit price_type matching debit/credit.
Stop and target are the single position_value condition in stop_rules / target_rules.
R04/R11 (risk/engine.payoff_max_loss) use Vertical.max_loss + fees, matching the spec's risk_reference.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

CENT = Decimal("0.01")
VERTICALS = {   # strategy: (option type, price type, long leg has the higher strike?)
    "bull_call_debit_spread": ("call", "debit", False),
    "bear_put_debit_spread": ("put", "debit", True),
    "bull_put_credit_spread": ("put", "credit", False),
    "bear_call_credit_spread": ("call", "credit", True),
}
# INV-15 (spec 1.1.2, validate_session.DIRECTION_OPS)
CONDITION_OPS = {"credit": {"stop": "gte", "target": "lte"}, "debit": {"stop": "lte", "target": "gte"}}
INV14_FROM, INV15_FROM = (1, 1, 1), (1, 1, 2)          # record schema_version where each invariant starts


def _ver(v: Optional[str]) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)
ROUNDING = {"debit": {"limit": "down", "stop": "up", "target": "down"},
            "credit": {"limit": "up", "stop": "down", "target": "up"}}


def D(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(repr(x)) if isinstance(x, float) else Decimal(str(x))


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT)


@dataclass(frozen=True)
class Vertical:
    strategy: str
    otype: str
    price_type: str
    long_strike: Decimal
    short_strike: Decimal
    width: Decimal
    price: Decimal
    qty: Decimal
    mult: Decimal

    @property
    def max_loss(self) -> Decimal:
        p = self.price if self.price_type == "debit" else self.width - self.price
        return p * self.mult * self.qty

    @property
    def max_profit(self) -> Decimal:
        p = self.width - self.price if self.price_type == "debit" else self.price
        return p * self.mult * self.qty

    def breakeven(self, at: Any = None) -> Decimal:
        p = self.price if at is None else D(at)
        if self.price_type == "debit":
            return self.long_strike + p if self.otype == "call" else self.long_strike - p
        return self.short_strike - p if self.otype == "put" else self.short_strike + p


def is_vertical(strategy: str) -> bool:
    return strategy in VERTICALS


def from_ticket(t: dict) -> tuple[Optional[Vertical], list[str]]:
    s = t.get("strategy")
    if s not in VERTICALS:
        return None, [f"{s} is not a vertical"]
    otype, ptype, long_high = VERTICALS[s]
    legs = t.get("legs") or []
    if len(legs) != 2:
        return None, [f"needs exactly 2 legs, has {len(legs)}"]
    errs = []
    if {l.get("type") for l in legs} != {otype}:
        errs.append(f"legs must both be {otype}s")
    if len({l.get("expiry") for l in legs}) != 1:
        errs.append("legs must share one expiry")
    if any(l.get("ratio", 1) != 1 for l in legs):
        errs.append("legs must be ratio 1:1")
    mults = {l.get("multiplier", 100) for l in legs}
    if mults != {100}:
        errs.append(f"leg multiplier must be 100, is {sorted(mults)}")
    buys = [l for l in legs if l.get("side") == "buy"]
    sells = [l for l in legs if l.get("side") == "sell"]
    if len(buys) != 1 or len(sells) != 1:
        errs.append("needs one buy and one sell leg")
    if errs:
        return None, errs
    lk, sk = D(buys[0]["strike"]), D(sells[0]["strike"])
    if lk == sk or (lk > sk) != long_high:
        return None, [f"strikes long {lk} / short {sk} do not form a {s}"]
    lp = t.get("limit_price") or {}
    if lp.get("price_type") != ptype:
        return None, [f"limit price_type must be {ptype}, is {lp.get('price_type')}"]
    if lp.get("amount") is None:
        return None, ["limit price missing"]
    q = t.get("quantity")
    if not isinstance(q, int) or q < 1:
        return None, [f"quantity must be a positive integer, is {q!r}"]
    return Vertical(s, otype, ptype, lk, sk, abs(lk - sk), D(lp["amount"]), D(q), D(100)), []


def _pv(t: dict, grp: str) -> list[dict]:
    return [c for r in t.get(grp) or [] for c in r.get("conditions") or [] if c.get("metric") == "position_value"]


def _tick_errs(where: str, value: Any, c: dict, need: str) -> list[str]:
    """value on its tick, rounded in the required (safe) direction from raw_value."""
    out = []
    tick, raw, rnd = c.get("tick_size"), c.get("raw_value", c.get("raw_amount")), c.get("rounding")
    if tick is None:
        return [f"{where} has no tick_size"]
    v, tk = D(value), D(tick)
    if tk <= 0 or v % tk != 0:
        out.append(f"{where} {v} is not on the {tk} tick")
    if rnd != need:
        out.append(f"{where} rounding is {rnd!r}, must be {need!r}")
    if raw is not None:
        r = D(raw)
        if need == "up" and not (r <= v < r + tk):
            out.append(f"{where} {v} is not raw {r} rounded up to the {tk} tick")
        if need == "down" and not (r - tk < v <= r):
            out.append(f"{where} {v} is not raw {r} rounded down to the {tk} tick")
    return out


_WHY = {    # Doodle's wording (validate_session.py 1.1.2), so messages match the spec validator verbatim
    ("debit", "stop"): "a debit spread is stopped out when its value falls to the stop",
    ("debit", "target"): "a debit spread hits its target when its value rises to the target",
    ("credit", "stop"): "a credit spread is stopped out when the buy-back cost rises to the stop",
    ("credit", "target"): "a credit spread hits its target when the buy-back cost falls to the target",
}


def direction_errors(t: dict) -> list[str]:
    """INV-15 (spec 1.1.2), mirrors validate_session.direction_errors: the op of the SINGLE position_value stop /
    target condition (the one INV-14 reads) must be exactly CONDITION_OPS[price_type][side]; strict, so lt/gt
    fail. A side with a missing or duplicate condition is skipped (INV-14 reports it)."""
    if not is_vertical(t.get("strategy", "")):
        return []
    ptype = VERTICALS[t["strategy"]][1]
    out = []
    for kind, grp in (("stop", "stop_rules"), ("target", "target_rules")):
        cs = _pv(t, grp)
        if len(cs) != 1:
            continue
        op, want = cs[0].get("op"), CONDITION_OPS[ptype][kind]
        if op != want:
            out.append(f"{kind} position_value op is {op!r}, must be {want!r} for a {ptype} vertical ({t['strategy']}): "
                       f"{_WHY[(ptype, kind)]} (position_value {want} {cs[0].get('value')})")
    return out


def validate_vertical(t: dict, schema_version: str = "1.1.6") -> list[str]:
    """Vertical checks for one ticket ([] = OK) as they apply to a record of `schema_version`:
    INV-14 from 1.1.1, plus INV-15 (stop/target op) from 1.1.2. Non-vertical strategies return []."""
    if not is_vertical(t.get("strategy", "")) or _ver(schema_version) < INV14_FROM:
        return []
    out15 = [f"INV-15 {m}" for m in direction_errors(t)] if _ver(schema_version) >= INV15_FROM else []
    return _inv14(t) + out15


def record_errors(rec: dict) -> list[str]:
    """INV-14/15 over every ticket of a session record, gated by the record's schema_version."""
    sv = rec.get("schema_version")
    return [f"{t.get('ticket_id')}: {e}" for t in rec.get("tickets") or [] for e in validate_vertical(t, sv)]


def _inv14(t: dict) -> list[str]:
    v, errs = from_ticket(t)
    if v is None:
        return errs
    out: list[str] = []
    w, p = v.width, v.price
    stops, tgts = _pv(t, "stop_rules"), _pv(t, "target_rules")
    if len(stops) != 1:
        out.append(f"needs exactly one position_value stop, has {len(stops)}")
    if len(tgts) != 1:
        out.append(f"needs exactly one position_value target, has {len(tgts)}")
    stop = D(stops[0]["value"]) if len(stops) == 1 else None
    tgt = D(tgts[0]["value"]) if len(tgts) == 1 else None
    if v.price_type == "credit":
        if not (0 < p < w):
            out.append(f"min credit {p} must satisfy 0 < credit < width {w}")
        if stop is not None and not (p < stop < w):
            out.append(f"stop debit {stop} must satisfy credit {p} < stop < width {w}")
        if tgt is not None and not (0 <= tgt < p):
            out.append(f"target buyback {tgt} must satisfy 0 <= target < credit {p}")
        if stop is not None and (loss := (stop - p) * v.mult * v.qty) > v.max_loss:
            out.append(f"planned loss at stop {money(loss)} > max_loss {money(v.max_loss)}")
    else:
        if not (0 < p < w):
            out.append(f"max debit {p} must satisfy 0 < debit < width {w}")
        if stop is not None and not (0 < stop < p):
            out.append(f"stop {stop} must satisfy 0 < stop < debit {p}")
        if tgt is not None and not (p < tgt <= w):
            out.append(f"target {tgt} must satisfy debit {p} < target <= width {w}")
    need = ROUNDING[v.price_type]
    out += _tick_errs("limit", p, t["limit_price"], need["limit"])
    if stop is not None:
        out += _tick_errs("stop", stop, stops[0], need["stop"])
    if tgt is not None:
        out += _tick_errs("target", tgt, tgts[0], need["target"])
    for key, want in (("max_loss", v.max_loss), ("max_profit", v.max_profit)):
        st = t.get(key) or {}
        got = st.get("amount_usd")
        if st.get("unlimited") or got is None or money(D(got)) != money(want):
            out.append(f"{key}.amount_usd {got} != computed {money(want)}")
        if st.get("at_price") is not None and D(st["at_price"]) != p:
            out.append(f"{key}.at_price {st['at_price']} != limit {p}")
    bes = t.get("breakevens") or []
    if not any(D(b["at_price"]) == p for b in bes):
        out.append(f"no breakeven at the limit {p} (computed {money(v.breakeven())})")
    for b in bes:
        want = v.breakeven(b["at_price"])
        if money(D(b["underlying_price"])) != money(want):
            out.append(f"breakeven {b['underlying_price']} at {b['at_price']} != computed {money(want)}")
    return out
