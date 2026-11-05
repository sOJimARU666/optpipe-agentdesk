"""INV-14 vertical-spread validation (Doodle's rule for spec 1.1.1, implemented before the re-pin).

Positive + negative cases for every constraint, both families (credit: bull put / bear call; debit: bull call /
bear put); planner tickets from the fixtures pass; R04 uses the max loss computed from legs + limit."""
from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from optpipe.planner.verticals import direction_errors, from_ticket, record_errors, validate_vertical


def _cond(value, raw, tick, rounding, op="lte"):
    return {"metric": "position_value", "op": op, "value": value, "raw_value": raw, "tick_size": tick,
            "rounding": rounding}


def ticket(strategy, long_k, short_k, price, stop, target, qty=2, *, raw=None, sraw=None, traw=None):
    """A self-consistent ticket; economics computed independently here (not via the module under test)."""
    otype = "call" if "call" in strategy else "put"
    credit = "credit" in strategy
    w = abs(long_k - short_k)
    d = {"limit": "up", "stop": "down", "target": "up"} if credit else {"limit": "down", "stop": "up", "target": "down"}
    ml = (w - price) * 100 * qty if credit else price * 100 * qty
    mp = price * 100 * qty if credit else (w - price) * 100 * qty
    if credit:
        be = short_k - price if otype == "put" else short_k + price
    else:
        be = long_k + price if otype == "call" else long_k - price
    return {
        "ticket_id": "tkt-test", "strategy": strategy, "quantity": qty,
        "legs": [{"leg_id": "leg-1", "type": otype, "side": "buy", "strike": long_k, "expiry": "2026-11-20",
                  "ratio": 1, "multiplier": 100},
                 {"leg_id": "leg-2", "type": otype, "side": "sell", "strike": short_k, "expiry": "2026-11-20",
                  "ratio": 1, "multiplier": 100}],
        "limit_price": {"amount": price, "raw_amount": price if raw is None else raw, "tick_size": 0.01,
                        "rounding": d["limit"], "price_type": "credit" if credit else "debit"},
        "stop_rules": [{"rule_id": "stop-1", "conditions": [_cond(stop, stop if sraw is None else sraw, 0.01, d["stop"],
                                                                  "gte" if credit else "lte")]}],
        "target_rules": [{"rule_id": "target-1", "conditions": [_cond(target, target if traw is None else traw, 0.01, d["target"],
                                                                      "lte" if credit else "gte")]}],
        "max_loss": {"amount_usd": round(ml, 2), "unlimited": False, "at_price": price},
        "max_profit": {"amount_usd": round(mp, 2), "unlimited": False, "at_price": price},
        "breakevens": [{"underlying_price": round(be, 2), "at_price": price}],
    }


# width 5; credit 1.80 (stop 3.60, target 0.90); debit 2.10 (stop 1.05, target 4.20)
GOOD = {
    "bull_put_credit_spread": ticket("bull_put_credit_spread", 95, 100, 1.80, 3.60, 0.90),
    "bear_call_credit_spread": ticket("bear_call_credit_spread", 105, 100, 1.80, 3.60, 0.90),
    "bull_call_debit_spread": ticket("bull_call_debit_spread", 100, 105, 2.10, 1.05, 4.20),
    "bear_put_debit_spread": ticket("bear_put_debit_spread", 100, 95, 2.10, 1.05, 4.20),
}
CREDIT = ["bull_put_credit_spread", "bear_call_credit_spread"]
DEBIT = ["bull_call_debit_spread", "bear_put_debit_spread"]


def _set(t, *, price=None, stop=None, target=None):
    t = copy.deepcopy(t)
    if price is not None:
        t["limit_price"].update(amount=price, raw_amount=price)
    if stop is not None:
        t["stop_rules"][0]["conditions"][0].update(value=stop, raw_value=stop)
    if target is not None:
        t["target_rules"][0]["conditions"][0].update(value=target, raw_value=target)
    return t


def _only(errs, needle):
    assert errs and any(needle in e for e in errs), errs


@pytest.mark.parametrize("strategy", list(GOOD))
def test_consistent_tickets_pass(strategy):
    assert validate_vertical(GOOD[strategy]) == []


def test_economics_from_legs():
    v, _ = from_ticket(GOOD["bull_put_credit_spread"])
    assert (v.width, v.max_loss, v.max_profit, v.breakeven()) == (5, Decimal("640.00"), Decimal("360.00"), Decimal("98.20"))
    v, _ = from_ticket(GOOD["bear_call_credit_spread"])
    assert v.breakeven() == Decimal("101.80")
    v, _ = from_ticket(GOOD["bull_call_debit_spread"])
    assert (v.max_loss, v.max_profit, v.breakeven()) == (Decimal("420.00"), Decimal("580.00"), Decimal("102.10"))
    v, _ = from_ticket(GOOD["bear_put_debit_spread"])
    assert v.breakeven() == Decimal("97.90")


# ---- credit constraints -------------------------------------------------------------------------------------
@pytest.mark.parametrize("s", CREDIT)
@pytest.mark.parametrize("price,ok", [(0.01, True), (4.99, True), (0, False), (5.0, False), (5.5, False)])
def test_credit_between_zero_and_width(s, price, ok):
    t = ticket(s, *((95, 100) if "put" in s else (105, 100)), price, min(4.99, price + 0.01), 0)
    errs = [e for e in validate_vertical(t) if "min credit" in e]
    assert (errs == []) is ok, errs


@pytest.mark.parametrize("s", CREDIT)
@pytest.mark.parametrize("stop,ok", [(1.81, True), (4.99, True), (1.80, False), (1.0, False), (5.0, False)])
def test_credit_stop_between_credit_and_width(s, stop, ok):
    errs = [e for e in validate_vertical(_set(GOOD[s], stop=stop)) if "stop debit" in e]
    assert (errs == []) is ok, errs


@pytest.mark.parametrize("s", CREDIT)
@pytest.mark.parametrize("tgt,ok", [(0, True), (1.79, True), (1.80, False), (2.5, False), (-0.05, False)])
def test_credit_target_below_credit(s, tgt, ok):
    errs = [e for e in validate_vertical(_set(GOOD[s], target=tgt)) if "target buyback" in e]
    assert (errs == []) is ok, errs


def test_credit_planned_loss_at_stop_vs_max_loss():
    # (stop - credit) <= width - credit  <=>  stop <= width: equal at the limit is OK, beyond it is not
    t = _set(GOOD["bull_put_credit_spread"], stop=4.99)
    assert not [e for e in validate_vertical(t) if "planned loss" in e]
    t = _set(GOOD["bull_put_credit_spread"], stop=5.01)
    _only(validate_vertical(t), "planned loss at stop 642.00 > max_loss 640.00")


# ---- debit constraints --------------------------------------------------------------------------------------
@pytest.mark.parametrize("s", DEBIT)
@pytest.mark.parametrize("price,ok", [(0.02, True), (4.99, True), (0, False), (5.0, False)])
def test_debit_between_zero_and_width(s, price, ok):
    t = ticket(s, 100, 105 if "call" in s else 95, price, max(0.01, price / 2), min(5, price + 0.01))
    errs = [e for e in validate_vertical(t) if "max debit" in e]
    assert (errs == []) is ok, errs


@pytest.mark.parametrize("s", DEBIT)
@pytest.mark.parametrize("stop,ok", [(0.01, True), (2.09, True), (0, False), (2.10, False), (3.0, False)])
def test_debit_stop_below_debit(s, stop, ok):
    errs = [e for e in validate_vertical(_set(GOOD[s], stop=stop)) if e.startswith("stop ")]
    assert (errs == []) is ok, errs


@pytest.mark.parametrize("s", DEBIT)
@pytest.mark.parametrize("tgt,ok", [(2.11, True), (5.0, True), (2.10, False), (1.0, False), (5.01, False)])
def test_debit_target_between_debit_and_width(s, tgt, ok):
    errs = [e for e in validate_vertical(_set(GOOD[s], target=tgt)) if e.startswith("target ")]
    assert (errs == []) is ok, errs


# ---- stored values must equal computed (exact to $0.01) -------------------------------------------------------
@pytest.mark.parametrize("s", list(GOOD))
@pytest.mark.parametrize("key", ["max_loss", "max_profit"])
def test_stored_money_must_match(s, key):
    t = copy.deepcopy(GOOD[s])
    t[key]["amount_usd"] += 0.01
    _only(validate_vertical(t), f"{key}.amount_usd")
    t[key]["amount_usd"] -= 0.01 - 0.004            # sub-cent float noise is fine
    assert validate_vertical(t) == []
    t[key]["unlimited"] = True
    _only(validate_vertical(t), f"{key}.amount_usd")


@pytest.mark.parametrize("s", list(GOOD))
def test_stored_breakeven_must_match(s):
    t = copy.deepcopy(GOOD[s])
    t["breakevens"][0]["underlying_price"] += 0.01
    _only(validate_vertical(t), "breakeven")
    t["breakevens"][0]["at_price"] = 2.0                  # none left at the limit
    _only(validate_vertical(t), "no breakeven at the limit")


def test_breakeven_at_other_price_uses_that_price():
    t = copy.deepcopy(GOOD["bull_call_debit_spread"])
    t["breakevens"].append({"underlying_price": 102.0, "at_price": 2.0})
    assert validate_vertical(t) == []
    t["breakevens"][-1]["underlying_price"] = 102.1
    _only(validate_vertical(t), "breakeven 102.1 at 2.0")


# ---- rounding: credit limit up / stop down / target up; debit the reverse ------------------------------------
@pytest.mark.parametrize("s,role,raw,val,ok", [
    ("bull_put_credit_spread", "limit", 1.795, 1.80, True), ("bull_put_credit_spread", "limit", 1.805, 1.80, False),
    ("bull_put_credit_spread", "stop", 3.605, 3.60, True), ("bull_put_credit_spread", "stop", 3.595, 3.60, False),
    ("bull_put_credit_spread", "target", 0.895, 0.90, True), ("bull_put_credit_spread", "target", 0.905, 0.90, False),
    ("bull_call_debit_spread", "limit", 2.105, 2.10, True), ("bull_call_debit_spread", "limit", 2.095, 2.10, False),
    ("bull_call_debit_spread", "stop", 1.045, 1.05, True), ("bull_call_debit_spread", "stop", 1.055, 1.05, False),
    ("bull_call_debit_spread", "target", 4.205, 4.20, True), ("bull_call_debit_spread", "target", 4.195, 4.20, False),
])
def test_rounding_direction_from_raw(s, role, raw, val, ok):
    t = copy.deepcopy(GOOD[s])
    tgt = (t["limit_price"] if role == "limit" else
           t[f"{role}_rules"][0]["conditions"][0])
    tgt["raw_amount" if role == "limit" else "raw_value"] = raw
    errs = [e for e in validate_vertical(t) if e.startswith(role)]
    assert (errs == []) is ok, errs


@pytest.mark.parametrize("s", list(GOOD))
def test_rounding_label_and_tick(s):
    t = copy.deepcopy(GOOD[s])
    want = t["stop_rules"][0]["conditions"][0]["rounding"]
    t["stop_rules"][0]["conditions"][0]["rounding"] = "down" if want == "up" else "up"
    _only(validate_vertical(t), f"must be '{want}'")
    t = copy.deepcopy(GOOD[s])
    t["target_rules"][0]["conditions"][0]["tick_size"] = 0.25
    _only(validate_vertical(t), "not on the 0.25 tick")


# ---- structure: everything comes from the legs ---------------------------------------------------------------
@pytest.mark.parametrize("mutate,needle", [
    (lambda t: t["legs"].pop(), "exactly 2 legs"),
    (lambda t: t["legs"][1].update(type="call"), "both be puts"),
    (lambda t: t["legs"][1].update(expiry="2026-12-18"), "one expiry"),
    (lambda t: t["legs"][1].update(ratio=2), "1:1"),
    (lambda t: [l.update(multiplier=10) for l in t["legs"]], "multiplier must be 100"),
    (lambda t: t["legs"][0].update(multiplier=50), "multiplier must be 100"),
    (lambda t: t["legs"][1].update(side="buy"), "one buy and one sell"),
    (lambda t: t["legs"][0].update(strike=105), "do not form"),
    (lambda t: t["limit_price"].update(price_type="debit"), "must be credit"),
    (lambda t: t.update(quantity=0), "positive integer"),
    (lambda t: t["stop_rules"][0]["conditions"].append(dict(t["stop_rules"][0]["conditions"][0])), "exactly one position_value stop"),
    (lambda t: t["target_rules"].clear(), "exactly one position_value target"),
])
def test_structure_errors(mutate, needle):
    t = copy.deepcopy(GOOD["bull_put_credit_spread"])
    mutate(t)
    _only(validate_vertical(t), needle)


# ---- INV-15 (spec 1.1.2): stop/target op direction, 1.1.2 records only ------------------------------------
OPS = {"credit": {"stop": "gte", "target": "lte"}, "debit": {"stop": "lte", "target": "gte"}}
ALL_OPS = ("gte", "lte", "gt", "lt", "eq")


@pytest.mark.parametrize("s", list(GOOD))
@pytest.mark.parametrize("kind", ["stop", "target"])
def test_inv15_direction_only_for_1_1_2_records(s, kind):
    fam = "credit" if "credit" in s else "debit"
    want = OPS[fam][kind]
    assert GOOD[s][f"{kind}_rules"][0]["conditions"][0]["op"] == want
    assert validate_vertical(GOOD[s], "1.1.2") == [] and direction_errors(GOOD[s]) == []
    for bad in set(ALL_OPS) - {want}:                    # strict: lt / gt fail too
        t = copy.deepcopy(GOOD[s])
        t[f"{kind}_rules"][0]["conditions"][0]["op"] = bad
        errs = validate_vertical(t, "1.1.2")
        assert len(errs) == 1 and errs[0].startswith(
            f"INV-15 {kind} position_value op is {bad!r}, must be {want!r} for a {fam} vertical ({s}): "), errs
        assert validate_vertical(t, "1.1.1") == [], "1.1.1 records are not affected by INV-15"
        assert validate_vertical(t, "1.1.0") == [] and validate_vertical(t, "1.0.0") == []


@pytest.mark.parametrize("s", list(GOOD))
@pytest.mark.parametrize("kind", ["stop", "target"])
@pytest.mark.parametrize("how", ["missing", "duplicate"])
def test_inv15_skips_a_side_inv14_already_flags(s, kind, how):
    t = copy.deepcopy(GOOD[s])
    rules = t[f"{kind}_rules"]
    wrong = next(o for o in ALL_OPS if o != rules[0]["conditions"][0]["op"])
    if how == "missing":
        rules[0]["conditions"][0]["metric"] = "underlying_price"
    else:
        rules[0]["conditions"].append({**rules[0]["conditions"][0], "op": wrong})
    assert direction_errors(t) == []
    errs = validate_vertical(t, "1.1.2")
    assert errs and not any(e.startswith("INV-15") for e in errs) and any("exactly one position_value" in e for e in errs)


def test_inv14_still_applies_to_1_1_2_records():
    t = copy.deepcopy(GOOD["bull_call_debit_spread"])
    t["max_loss"]["amount_usd"] += 1
    assert validate_vertical(t, "1.1.0") == []
    assert validate_vertical(t, "1.1.1") == validate_vertical(t, "1.1.2") != []


def test_record_errors_gated_by_schema_version():
    t = copy.deepcopy(GOOD["bear_call_credit_spread"])
    t["target_rules"][0]["conditions"][0]["op"] = "gte"
    t["ticket_id"] = "tkt-x"
    assert record_errors({"schema_version": "1.1.1", "tickets": [t]}) == []
    errs = record_errors({"schema_version": "1.1.2", "tickets": [t]})
    assert len(errs) == 1 and errs[0].startswith("tkt-x: INV-15 target position_value op is 'gte', must be 'lte'")


@pytest.mark.parametrize("s", list(GOOD))
@pytest.mark.parametrize("kind", ["stop", "target"])
@pytest.mark.parametrize("op", ALL_OPS)
def test_inv15_parity_with_spec_direction_errors(validator, s, kind, op):
    """Same messages as the pinned spec's validate_session.direction_errors (verbatim)."""
    if not hasattr(validator, "direction_errors"):
        pytest.skip("spec < 1.1.2")
    t = copy.deepcopy(GOOD[s])
    t[f"{kind}_rules"][0]["conditions"][0]["op"] = op
    assert direction_errors(t) == validator.direction_errors(copy.deepcopy(t))


def test_planner_records_are_1_1_3_and_pass_inv15(pipeline_output, validator):
    rec = pipeline_output["record"]
    assert rec["schema_version"] == "1.1.8" and rec["tickets"] and record_errors(rec) == []
    assert [e for e in validator.validate(copy.deepcopy(rec)) if e.startswith(("INV-14", "INV-15"))] == []
    bad = copy.deepcopy(rec)
    tk = next(t for t in bad["tickets"] if t["strategy"] in GOOD)
    pv = next(c for r in tk["stop_rules"] for c in r["conditions"] if c["metric"] == "position_value")
    pv["op"] = {"gte": "gt", "lte": "lt"}[pv["op"]]                          # strict: gt/lt are wrong
    mine = record_errors(bad)
    theirs = [e for e in validator.validate(copy.deepcopy(bad)) if e.startswith("INV-15")]
    assert len(mine) == len(theirs) == 1 and theirs[0] == "INV-15 " + mine[0].split(": INV-15 ", 1)[0] + " " + mine[0].split(": INV-15 ", 1)[1]
    assert record_errors({**bad, "schema_version": "1.1.1"}) == []


def test_stop_found_among_other_conditions():
    """the position_value condition is picked out of a combined stop rule (underlying close + value)."""
    t = copy.deepcopy(GOOD["bull_call_debit_spread"])
    t["stop_rules"][0]["conditions"].insert(0, {"metric": "underlying_close", "op": "lt", "value": 98})
    assert validate_vertical(t) == []


def test_width_and_qty_come_from_legs_not_stored_fields():
    t = copy.deepcopy(GOOD["bull_call_debit_spread"])
    t["width"] = 50                                  # stray stored field is ignored
    assert validate_vertical(t) == []
    t["legs"][1]["strike"] = 110                     # width 10 from the legs: stored max_profit now wrong
    _only(validate_vertical(t), "max_profit.amount_usd 580.0 != computed 1580.00")
    t = copy.deepcopy(GOOD["bull_call_debit_spread"]); t["quantity"] = 3
    _only(validate_vertical(t), "max_loss.amount_usd 420.0 != computed 630.00")


def test_decimal_exact_no_float_noise():
    t = ticket("bull_call_debit_spread", 100, 105, 0.3, 0.1, 0.7, qty=3)   # 0.3*3*100 = 90.00000000000001 in float
    assert validate_vertical(t) == []


def test_non_vertical_is_ignored():
    assert validate_vertical({"strategy": "long_call"}) == []


# ---- pipeline: every planner ticket is checked; fixtures pass; R04 is computed ------------------------------
def test_fixture_planner_tickets_pass_inv14(rec):
    tickets = rec["tickets"]
    assert tickets, "fixture run produced no tickets"
    assert {t["ticket_id"]: validate_vertical(t) for t in tickets} == {t["ticket_id"]: [] for t in tickets}


def test_every_planner_ticket_is_checked_and_fails_closed(monkeypatch, tmp_path, rec):
    """Swap in a validator that rejects everything: the run must emit no tickets and say why."""
    from optpipe.cli import main
    from optpipe.planner import builder
    from conftest import FIXTURES
    seen = []
    monkeypatch.setattr(builder, "validate_vertical", lambda t, sv=None: seen.append(t["ticket_id"]) or ["synthetic error"])
    # (the builder passes the schema version it writes; INV-15 joins once that is 1.1.2)
    assert main(["run", "--fixtures", str(FIXTURES), "--out", str(tmp_path)]) in (0, 1)
    out = json.loads((tmp_path / "session-2026-10-13.json").read_text())
    assert set(seen) >= {t["ticket_id"] for t in rec["tickets"]}, "every ticket the planner builds is validated"
    assert out["tickets"] == []
    assert any("INV-14: synthetic error" in json.dumps(e) for e in out["idea_log"])


@pytest.mark.parametrize("s,legs,price,want", [
    ("bull_call_debit_spread", (("buy", 340), ("sell", 350)), 4.25, 425.0),
    ("bear_call_credit_spread", (("sell", 340), ("buy", 350)), 4.25, 575.0),
])
def test_r04_uses_computed_max_loss(raw_rules, spec, s, legs, price, want):
    from optpipe.risk import evaluate, load_ruleset
    ruleset, _ = load_ruleset(spec)
    inp = copy.deepcopy(raw_rules["fixture"])
    inp["ticket"].update(strategy=s, limit_price={"amount": price, "price_type": "credit" if "credit" in s else "debit"})
    inp["ticket"]["max_loss"] = {"amount_usd": 1.0, "unlimited": False}      # a stored value R04 must ignore
    for leg, (side, k) in zip(inp["legs"], legs):
        leg.update(side=side, strike=k)
    fees = Decimal(str(raw_rules["config"].get("fees_per_contract_usd") or 0)) * 2 * inp["ticket"]["quantity"]
    try:
        res = evaluate(inp, ruleset)
    except Exception:                                          # input model may forbid the stray max_loss key
        inp["ticket"].pop("max_loss")
        res = evaluate(inp, ruleset)
    out = res.model_dump(mode="json")
    r04 = next(r for r in out["results"] if r["rule_id"] == "R04_MAX_LOSS")
    assert Decimal(str(r04["measured"]["max_loss_usd"])) == Decimal(str(want)) + fees
    t = ticket(s, legs[0][1] if legs[0][0] == "buy" else legs[1][1], legs[1][1] if legs[1][0] == "sell" else legs[0][1],
               price, 0, 0, qty=inp["ticket"]["quantity"])
    v, _ = from_ticket(t)
    assert v.max_loss == Decimal(str(want))


# ---- parity with Doodle's validator (spec 1.1.1 validate_session.vertical_errors) ---------------------------
_MUTATIONS = [
    lambda t: None,
    lambda t: t["limit_price"].update(amount=0, raw_amount=0),
    lambda t: t["stop_rules"][0]["conditions"][0].update(value=0.5, raw_value=0.5),
    lambda t: t["stop_rules"][0]["conditions"][0].update(value=6.0, raw_value=6.0),
    lambda t: t["target_rules"][0]["conditions"][0].update(value=1.9, raw_value=1.9),
    lambda t: t["target_rules"][0]["conditions"][0].update(value=5.5, raw_value=5.5),
    lambda t: t["max_loss"].update(amount_usd=t["max_loss"]["amount_usd"] + 0.02),
    lambda t: t["max_profit"].update(amount_usd=t["max_profit"]["amount_usd"] - 0.02),
    lambda t: t["breakevens"][0].update(underlying_price=t["breakevens"][0]["underlying_price"] + 0.05),
    lambda t: t["legs"][1].update(expiry="2026-12-18"),
    lambda t: t["legs"][1].update(ratio=2),
    lambda t: t["legs"][0].update(multiplier=50),
    lambda t: t["legs"][1].update(side="buy"),
    lambda t: t["limit_price"].update(price_type="credit" if t["limit_price"]["price_type"] == "debit" else "debit"),
    lambda t: t["target_rules"].clear(),
    lambda t: t.update(quantity=3),
]


@pytest.mark.parametrize("s", list(GOOD))
@pytest.mark.parametrize("m", range(len(_MUTATIONS)))
def test_parity_with_spec_vertical_errors(validator, s, m):
    if not hasattr(validator, "vertical_errors"):
        pytest.skip("spec < 1.1.1")
    t = copy.deepcopy(GOOD[s])
    _MUTATIONS[m](t)
    spec_errs = validator.vertical_errors(copy.deepcopy(t))
    extras = ("tick", "rounding is", "rounded")                       # INV-13 tick checks: not part of INV-14
    mine = [e for e in validate_vertical(t, "1.1.1") if not any(x in e for x in extras)]   # INV-14 only
    assert bool(mine) == bool(spec_errs), (mine, spec_errs)


def test_one_record_version_everywhere():
    from optpipe import orchestrator
    from optpipe.records import compat
    assert orchestrator.SCHEMA_VERSION == compat.V11 == "1.1.8" and {"1.1.2", "1.1.3", "1.1.4", "1.1.5", "1.1.6", "1.1.7", "1.1.8"} <= set(compat.V11_SHAPE)
