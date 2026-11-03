"""pytest: schema 1.1.1 - INV-14 vertical consistency (all four verticals, every clause), INV-13 credit rounding, R04 computed max loss."""
import copy
import json
from pathlib import Path

import pytest

import risk_reference as rr
import validate_session as vs
from test_schema_v1_1 import requality

SPEC = Path(__file__).resolve().parent
EXAMPLE = json.loads((SPEC / "session.example.json").read_text())

# strategy: (type, long strike, short strike, price_type, price, stop, target, max_loss, max_profit, breakeven)  - width 10, qty 1
BASE = {
    "bull_call_debit_spread": ("call", 100, 110, "debit", 4.00, 2.00, 8.00, 400, 600, 104),
    "bear_put_debit_spread": ("put", 110, 100, "debit", 4.00, 2.00, 8.00, 400, 600, 106),
    "bull_put_credit_spread": ("put", 100, 110, "credit", 3.00, 6.00, 1.00, 700, 300, 107),
    "bear_call_credit_spread": ("call", 110, 100, "credit", 3.00, 6.00, 1.00, 700, 300, 103),
}
DEBIT = [k for k, v in BASE.items() if v[3] == "debit"]
CREDIT = [k for k, v in BASE.items() if v[3] == "credit"]
ALL = list(BASE)


def pv(op, v):
    return {"metric": "position_value", "op": op, "value": v}


def mk(kind, qty=1, **kw):
    typ, lk, sk, ptype, price, stop, tgt, ml, mp, be = BASE[kind]
    price, stop, tgt = kw.get("price", price), kw.get("stop", stop), kw.get("target", tgt)
    leg = lambda i, side, k: {"leg_id": f"leg-{i}", "contract_id": f"X{i}", "type": typ, "side": side, "strike": float(k),
                              "expiry": "2026-11-20", "ratio": 1, "multiplier": 100}
    credit = ptype == "credit"
    return {
        "ticket_id": f"t-{kind}", "strategy": kind, "underlying": "XYZ", "quantity": qty,
        "legs": [leg(1, "buy", lk), leg(2, "sell", sk)],
        "limit_price": {"amount": price, "price_type": ptype},
        "stop_rules": [{"rule_id": "s", "text": "", "combine": "any", "conditions": [pv("gte" if credit else "lte", stop)]}],
        "target_rules": [{"rule_id": "g", "text": "", "combine": "any", "conditions": [pv("lte" if credit else "gte", tgt)]}],
        "max_loss": {"amount_usd": kw.get("ml", ml * qty), "unlimited": False, "at_price": price},
        "max_profit": {"amount_usd": kw.get("mp", mp * qty), "unlimited": False, "at_price": price},
        "breakevens": [{"underlying_price": kw.get("be", be), "at_price": price}],
    }


def errs(t):
    return vs.vertical_errors(t)


def has(t, text):
    e = errs(t)
    assert any(text in x for x in e), e


# ---------------------------------------------------------------- pass cases
@pytest.mark.parametrize("kind", ALL)
@pytest.mark.parametrize("qty", [1, 3])
def test_valid_vertical(kind, qty):
    assert errs(mk(kind, qty)) == []


@pytest.mark.parametrize("kind", ALL)
def test_boundaries_and_tolerance_pass(kind):
    credit = BASE[kind][3] == "credit"
    t = mk(kind, target=0.0) if credit else mk(kind, target=10.0)      # target_buyback >= 0 / target <= width are inclusive
    assert errs(t) == []
    t["max_loss"]["amount_usd"] += 0.01; t["max_profit"]["amount_usd"] -= 0.01; t["breakevens"][0]["underlying_price"] += 0.01
    assert errs(t) == []                                                # 0.01 tolerance on $ amounts


@pytest.mark.parametrize("kind", ALL)
def test_extra_breakeven_at_other_price(kind):
    t = mk(kind); typ, lk, sk, ptype, *_ = BASE[kind]
    ref = {"bull_call_debit_spread": 103.9, "bear_put_debit_spread": 106.1, "bull_put_credit_spread": 107.1, "bear_call_credit_spread": 102.9}[kind]
    t["breakevens"].append({"underlying_price": ref, "at_price": 3.9 if ptype == "debit" else 2.9})
    assert errs(t) == []
    t["breakevens"][-1]["underlying_price"] = ref + 0.5
    has(t, "breakeven")


def test_float_noise_limit():
    t = mk("bull_call_debit_spread", price=3.025 * 1.025)   # 3.1006249999999995 (float noise)
    t["max_loss"].update(amount_usd=310.06, at_price=t["limit_price"]["amount"]); t["max_profit"].update(amount_usd=689.94, at_price=t["limit_price"]["amount"])
    t["breakevens"] = [{"underlying_price": 103.10, "at_price": t["limit_price"]["amount"]}]
    assert errs(t) == []


# ---------------------------------------------------------------- credit clauses (bull put + bear call)
@pytest.mark.parametrize("kind", CREDIT)
@pytest.mark.parametrize("price", [0.0, 10.0, 10.5])
def test_credit_bounds(kind, price):
    has(mk(kind, price=price), "must satisfy 0 < credit < width")


@pytest.mark.parametrize("kind", CREDIT)
@pytest.mark.parametrize("stop", [3.0, 2.5, 10.0])
def test_credit_stop_bounds(kind, stop):
    has(mk(kind, stop=stop), "must satisfy credit 3")


@pytest.mark.parametrize("kind", CREDIT)
@pytest.mark.parametrize("target", [-0.05, 3.0, 3.5])
def test_credit_target_bounds(kind, target):
    has(mk(kind, target=target), "target buyback")


@pytest.mark.parametrize("kind", CREDIT)
def test_credit_planned_loss_at_stop(kind):
    t = mk(kind, stop=10.5)       # (10.5 - 3) * 100 = 750 > max_loss 700 (only reachable with stop >= width)
    has(t, "planned loss at stop 750")
    assert errs(mk(kind, stop=9.95)) == []


@pytest.mark.parametrize("kind", CREDIT)
def test_credit_max_loss_uses_limit_credit(kind):
    has(mk(kind, ml=650), "max_loss.amount_usd 650 != computed")          # e.g. computed at a better 3.50 credit
    has(mk(kind, qty=2, ml=700, mp=600), "max_loss.amount_usd 700 != computed 1400")


@pytest.mark.parametrize("kind", CREDIT)
def test_credit_max_profit(kind):
    has(mk(kind, mp=350), "max_profit.amount_usd 350 != computed 300")


@pytest.mark.parametrize("kind,wrong", [("bull_put_credit_spread", 103), ("bear_call_credit_spread", 107)])
def test_credit_breakeven_side(kind, wrong):
    has(mk(kind, be=wrong), "!= computed")


# ---------------------------------------------------------------- debit clauses (bull call + bear put)
@pytest.mark.parametrize("kind", DEBIT)
@pytest.mark.parametrize("price", [0.0, 10.0, 12.0])
def test_debit_bounds(kind, price):
    has(mk(kind, price=price), "must satisfy 0 < debit < width")


@pytest.mark.parametrize("kind", DEBIT)
@pytest.mark.parametrize("stop", [0.0, 4.0, 4.5])
def test_debit_stop_bounds(kind, stop):
    has(mk(kind, stop=stop), "must satisfy 0 < stop < debit")


@pytest.mark.parametrize("kind", DEBIT)
@pytest.mark.parametrize("target", [4.0, 3.0, 10.05])
def test_debit_target_bounds(kind, target):
    has(mk(kind, target=target), "must satisfy debit 4")


@pytest.mark.parametrize("kind", DEBIT)
def test_debit_max_loss_and_profit(kind):
    has(mk(kind, ml=402), "max_loss.amount_usd 402 != computed 400")
    has(mk(kind, mp=598), "max_profit.amount_usd 598 != computed 600")
    has(mk(kind, qty=2, ml=400, mp=1200), "computed 800")


@pytest.mark.parametrize("kind,wrong", [("bull_call_debit_spread", 96), ("bear_put_debit_spread", 114)])
def test_debit_breakeven_side(kind, wrong):
    has(mk(kind, be=wrong), "!= computed")


# ---------------------------------------------------------------- structure / stored-field clauses (all four)
@pytest.mark.parametrize("kind", ALL)
def test_structure_errors(kind):
    t = mk(kind); t["limit_price"]["price_type"] = "credit" if BASE[kind][3] == "debit" else "debit"
    has(t, "price_type must be")
    t = mk(kind); t["legs"][0]["strike"], t["legs"][1]["strike"] = t["legs"][1]["strike"], t["legs"][0]["strike"]
    has(t, "do not form this vertical")
    t = mk(kind); t["legs"].append(copy.deepcopy(t["legs"][0])); has(t, "exactly 2 legs")
    t = mk(kind); t["legs"][0]["type"] = "put" if t["legs"][0]["type"] == "call" else "call"; has(t, "legs must both be")
    t = mk(kind); t["legs"][0]["ratio"] = 2; has(t, "ratio 1:1")
    t = mk(kind); t["legs"][0]["expiry"] = "2026-12-18"; has(t, "one expiry")
    t = mk(kind); t["legs"][1]["side"] = "buy"; has(t, "one buy and one sell")
    t = mk(kind); t["legs"][0]["multiplier"] = 10; has(t, "multiplier must be 100")


@pytest.mark.parametrize("kind", ALL)
def test_stop_target_presence(kind):
    t = mk(kind); t["stop_rules"] = []; has(t, "one position_value stop")
    t = mk(kind); t["target_rules"][0]["conditions"].append(copy.deepcopy(t["target_rules"][0]["conditions"][0])); has(t, "one position_value target")


@pytest.mark.parametrize("kind", ALL)
def test_stored_payoff_fields(kind):
    t = mk(kind); t["max_loss"]["unlimited"] = True; has(t, "max_loss.amount_usd")
    t = mk(kind); t["max_profit"]["at_price"] = 3.5; has(t, "max_profit.at_price 3.5 != limit")
    t = mk(kind); t["breakevens"][0]["at_price"] = 3.5; has(t, "no breakeven at the limit")
    t = mk(kind); t["max_loss"]["amount_usd"] += 0.02; has(t, "max_loss.amount_usd")


# ---------------------------------------------------------------- R04 uses the computed (INV-14) max loss
@pytest.mark.parametrize("kind", ALL)
def test_r04_uses_vertical_max_loss(kind):
    t = mk(kind, qty=2)
    inp = {"legs": t["legs"], "ticket": {"strategy": kind, "quantity": 2, "limit_price": t["limit_price"]}}
    cfg = dict(rr.load_rules()["config"])
    out = rr.payoff_max_loss(inp, cfg)
    assert out == {"max_loss_usd": BASE[kind][7] * 2.0, "unlimited": False, "basis": "vertical"}
    cfg["fees_per_contract_usd"] = "0.65"                                     # fees still added: 0.65 * 2 qty * 2 legs
    assert rr.payoff_max_loss(inp, cfg)["max_loss_usd"] == pytest.approx(BASE[kind][7] * 2 + 2.6)
    inp["ticket"]["strategy"] = "other"                                       # non-vertical -> generic payoff, same number here
    assert rr.payoff_max_loss(inp, dict(rr.load_rules()["config"]))["max_loss_usd"] == BASE[kind][7] * 2.0


# ---------------------------------------------------------------- integration through validate()
def ex(version="1.1.1"):
    s = copy.deepcopy(EXAMPLE); s["schema_version"] = version
    return s


def tk(s):
    return s["tickets"][0]


def sync_approvals(s):
    for a in s["approvals"]:
        if a["ticket_id"] == tk(s)["ticket_id"]:
            a["legs"] = copy.deepcopy(tk(s)["legs"]); a["limit_price"] = copy.deepcopy(tk(s)["limit_price"])
    return requality(s)


def test_example_is_1_1_2_and_passes_inv14():
    assert EXAMPLE["schema_version"] == "1.1.5" and vs.validate(EXAMPLE) == []   # example tracks current version
    assert vs.vertical_errors(tk(EXAMPLE)) == []


def test_inv14_only_for_1_1_1_and_later():
    for version in ("1.1.0", "1.1.1", "1.1.2", "1.1.3", "1.1.4", "1.1.5"):
        s = ex(version); tk(s)["max_loss"]["amount_usd"] = 350.0
        e = [x for x in vs.validate(s) if x.startswith("INV-14")]
        assert bool(e) == (version != "1.1.0"), e


def credit_session(version="1.1.1", lim=("up", 4.00), stop=("down", 7.95), tgt=("up", 1.99)):
    """example JPM 340/350 calls turned into a bear call credit spread (sell 340C, buy 350C), penny-pilot ticks."""
    s = ex(version); t = tk(s)
    t["strategy"] = "bear_call_credit_spread"
    t["legs"][0]["side"], t["legs"][1]["side"] = "sell", "buy"
    t["limit_price"] = {"amount": lim[1], "price_type": "credit", "raw_amount": 3.98 if lim[0] == "up" else 4.03, "tick_size": 0.05, "rounding": lim[0]}
    t["entry_rules"] = [{"rule_id": "entry-1", "text": "credit >= 4.00", "combine": "all", "conditions": [{"metric": "net_credit_mid", "op": "gte", "value": 4.0}]}]
    t["stop_rules"] = [{"rule_id": "stop-1", "text": "buy back at 7.95", "combine": "any", "conditions": [
        {"metric": "position_value", "op": "gte", "value": stop[1], "raw_value": 7.97 if stop[0] == "down" else 7.93, "tick_size": 0.05, "rounding": stop[0]}]}]
    t["target_rules"] = [{"rule_id": "target-1", "text": "buy back at 1.99", "combine": "any", "conditions": [
        {"metric": "position_value", "op": "lte", "value": tgt[1], "raw_value": 1.984 if tgt[0] == "up" else 1.996, "tick_size": 0.01, "rounding": tgt[0]}]}]
    t["max_loss"] = {"amount_usd": 600.0, "unlimited": False, "at_price": 4.0, "basis": "(10 - 4.00) x 100"}
    t["max_profit"] = {"amount_usd": 400.0, "unlimited": False, "at_price": 4.0, "basis": "4.00 x 100"}
    t["breakevens"] = [{"underlying_price": 344.0, "at_price": 4.0}]
    return sync_approvals(s)


def inv(s, p):
    return [x for x in vs.validate(s) if x.startswith(p)]


def test_credit_rounding_valid():
    assert vs.validate(credit_session()) == []


def test_credit_limit_must_round_up():
    assert any("limit_price rounding 'down', must be 'up'" in x for x in inv(credit_session(lim=("down", 4.00)), "INV-13"))


def test_credit_stop_must_round_down():
    assert any("stop_rules" in x and "must be 'down'" in x for x in inv(credit_session(stop=("up", 7.95)), "INV-13"))


def test_credit_target_must_round_up():
    assert any("target_rules" in x and "must be 'up'" in x for x in inv(credit_session(tgt=("down", 1.99)), "INV-13"))


def test_credit_rounded_value_checked():
    s = credit_session(); tk(s)["limit_price"]["amount"] = 3.95; sync_approvals(s)   # 3.98 up @0.05 = 4.00, not 3.95
    assert any("rounded 3.95" in x for x in inv(s, "INV-13"))


def test_credit_directions_not_enforced_in_1_1_0_limit():
    # 1.1.0 keeps its frozen rules: no forced direction on a credit limit (stop/target keep the debit-style 1.1.0 rule)
    s = credit_session("1.1.0", lim=("down", 4.00))
    assert not any("limit_price rounding" in x for x in inv(s, "INV-13"))


def test_credit_session_inv14_fail_propagates():
    s = credit_session(); tk(s)["breakevens"][0]["underlying_price"] = 336.0       # put-side formula on a call spread
    assert any("breakeven 336.0" in x for x in inv(s, "INV-14"))
