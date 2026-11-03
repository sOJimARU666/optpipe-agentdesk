"""pytest: schema 1.1.2 - INV-15 stop/target comparison direction for the four verticals, plus version gating.

Debit verticals (bull call / bear put): stop position_value op lte, target op gte.
Credit verticals (bull put / bear call; position_value = cost to buy back): stop op gte, target op lte.
"""
import copy

import pytest

import validate_session as vs
from test_verticals_v1_1_1 import ALL, BASE, CREDIT, DEBIT, credit_session, ex, mk, sync_approvals, tk

WANT = {"debit": {"stop": "lte", "target": "gte"}, "credit": {"stop": "gte", "target": "lte"}}
OPS = ["lt", "lte", "gt", "gte", "eq", "cross_above", "cross_below"]
GRP = {"stop": "stop_rules", "target": "target_rules"}


def ptype(kind):
    return BASE[kind][3]


def with_op(t, side, op):
    """set the op of the single position_value condition in stop_rules / target_rules"""
    for c in t[GRP[side]][0]["conditions"]:
        if c["metric"] == "position_value":
            c["op"] = op
    return t


def inv15(s):
    return [x for x in vs.validate(s) if x.startswith("INV-15")]


# ---------------------------------------------------------------- unit: direction_errors (all four verticals)
@pytest.mark.parametrize("kind", ALL)
def test_correct_directions_pass(kind):
    t = mk(kind)
    assert t["stop_rules"][0]["conditions"][0]["op"] == WANT[ptype(kind)]["stop"]
    assert t["target_rules"][0]["conditions"][0]["op"] == WANT[ptype(kind)]["target"]
    assert vs.direction_errors(t) == [] and vs.vertical_errors(t) == []


@pytest.mark.parametrize("kind", ALL)
@pytest.mark.parametrize("side", ["stop", "target"])
@pytest.mark.parametrize("op", OPS)
def test_every_wrong_op_fails(kind, side, op):
    want = WANT[ptype(kind)][side]
    e = vs.direction_errors(with_op(mk(kind), side, op))
    if op == want:
        assert e == []
    else:
        assert len(e) == 1, e
        assert e[0].startswith(f"{side} position_value op is {op!r}, must be {want!r} for a {ptype(kind)} vertical ({kind})"), e


@pytest.mark.parametrize("kind", ALL)
def test_reversed_directions_fail_both(kind):
    p = ptype(kind)
    t = with_op(with_op(mk(kind), "stop", WANT[p]["target"]), "target", WANT[p]["stop"])
    e = vs.direction_errors(t)
    assert [x.split()[0] for x in e] == ["stop", "target"], e
    assert vs.vertical_errors(t) == []          # INV-14 does not look at the op


@pytest.mark.parametrize("kind", CREDIT)
def test_credit_messages_explain_buyback(kind):
    e = vs.direction_errors(with_op(with_op(mk(kind), "stop", "lte"), "target", "gte"))
    assert "buy-back cost rises to the stop" in e[0] and "position_value gte 6.0" in e[0]
    assert "buy-back cost falls to the target" in e[1] and "position_value lte 1.0" in e[1]


@pytest.mark.parametrize("kind", DEBIT)
def test_debit_messages_explain_value(kind):
    e = vs.direction_errors(with_op(with_op(mk(kind), "stop", "gte"), "target", "lte"))
    assert "value falls to the stop" in e[0] and "position_value lte 2.0" in e[0]
    assert "value rises to the target" in e[1] and "position_value gte 8.0" in e[1]


@pytest.mark.parametrize("kind", ALL)
def test_other_conditions_are_not_checked(kind):
    t = mk(kind)
    t["stop_rules"][0]["conditions"].append({"metric": "underlying_close", "op": "gt", "value": 1})
    t["target_rules"][0]["conditions"].append({"metric": "underlying_last", "op": "lt", "value": 1})
    assert vs.direction_errors(t) == []


@pytest.mark.parametrize("kind", ALL)
def test_missing_or_duplicate_left_to_inv14(kind):
    t = mk(kind); t["stop_rules"] = []
    t["target_rules"][0]["conditions"].append(copy.deepcopy(t["target_rules"][0]["conditions"][0]))
    with_op(t, "target", "eq")
    assert vs.direction_errors(t) == []
    assert any("one position_value stop" in x for x in vs.vertical_errors(t))
    assert any("one position_value target" in x for x in vs.vertical_errors(t))


@pytest.mark.parametrize("kind", ALL)
def test_reads_same_condition_as_inv14(kind):
    """the stop is found in a second stop rule just as INV-14 finds it"""
    t = mk(kind); c = t["stop_rules"][0]["conditions"].pop()
    t["stop_rules"].append({"rule_id": "s2", "text": "", "combine": "any", "conditions": [c]})
    assert vs.vertical_errors(t) == [] and vs.direction_errors(t) == []
    t["stop_rules"][1]["conditions"][0]["op"] = "eq"
    assert len(vs.direction_errors(t)) == 1


# ---------------------------------------------------------------- integration through validate(): example JPM (debit) and credit session
def debit_session(version="1.1.2", stop="lte", target="gte"):
    s = ex(version); t = tk(s)
    for grp, op in (("stop_rules", stop), ("target_rules", target)):
        for r in t[grp]:
            for c in r["conditions"]:
                if c["metric"] == "position_value":
                    c["op"] = op
    return sync_approvals(s)


def credit_112(stop="gte", target="lte", version="1.1.2"):
    s = credit_session(version)
    tk(s)["stop_rules"][0]["conditions"][0]["op"] = stop
    tk(s)["target_rules"][0]["conditions"][0]["op"] = target
    return sync_approvals(s)


def test_example_jpm_debit_uses_lte_stop_gte_target():
    s = ex("1.1.2"); t = tk(s)
    assert t["strategy"] == "bull_call_debit_spread"
    assert vs._pv(t, "stop_rules")[0]["op"] == "lte" and vs._pv(t, "target_rules")[0]["op"] == "gte"
    assert vs.validate(s) == []


@pytest.mark.parametrize("side,op", [("stop", "gte"), ("stop", "lt"), ("target", "lte"), ("target", "gt")])
def test_debit_wrong_direction_fails_in_1_1_2(side, op):
    s = debit_session(**{side: op})
    e = inv15(s)
    assert len(e) == 1 and e[0].startswith(f"INV-15 tkt-jpm-bcs-340-350 {side} position_value op is {op!r}"), e
    assert [x for x in vs.validate(s) if not x.startswith("INV-15")] == []


def test_credit_correct_direction_passes_in_1_1_2():
    assert vs.validate(credit_112()) == []


@pytest.mark.parametrize("side,op", [("stop", "lte"), ("stop", "gt"), ("target", "gte"), ("target", "lt")])
def test_credit_wrong_direction_fails_in_1_1_2(side, op):
    s = credit_112(**{side: op})
    e = inv15(s)
    assert len(e) == 1 and e[0].startswith(f"INV-15 tkt-jpm-bcs-340-350 {side} position_value op is {op!r}"), e
    assert "bear_call_credit_spread" in e[0]


# ---------------------------------------------------------------- version gating: same wrong directions still pass in older records
@pytest.mark.parametrize("version", ["1.1.0", "1.1.1"])
def test_debit_wrong_directions_pass_before_1_1_2(version):
    assert vs.validate(debit_session(version, stop="gte", target="lte")) == []


def test_credit_wrong_directions_pass_in_1_1_1():
    assert vs.validate(credit_112(stop="lte", target="gte", version="1.1.1")) == []


def test_debit_wrong_directions_pass_in_1_0_0():
    from test_schema_v1_1_ticks import strip_v1_1
    s = strip_v1_1(debit_session("1.1.2", stop="gte", target="lte")); s["schema_version"] = "1.0.0"
    assert vs.validate(sync_approvals(s)) == []


def test_1_1_2_keeps_1_1_1_checks():
    s = credit_session("1.1.2", lim=("down", 4.00))                  # INV-13 credit rounding still enforced
    assert any("limit_price rounding 'down', must be 'up'" in x for x in vs.validate(s))
    s = ex("1.1.2"); tk(s)["max_loss"]["amount_usd"] = 350.0           # INV-14 still enforced
    assert any(x.startswith("INV-14") for x in vs.validate(s))


def test_schema_accepts_1_1_2_only_as_listed():
    assert vs.validate(ex("1.1.2")) == []
    assert vs.validate(ex("1.1.3")) == []   # spec 1.1.3 lists 1.1.3 (was the unlisted probe before 1.1.3)
    assert vs.validate(ex("1.1.4")) == []   # spec 1.1.4 lists 1.1.4 (was the unlisted probe before 1.1.4)
    assert vs.validate(ex("1.1.5")) == []   # spec 1.1.5 lists 1.1.5 (was the unlisted probe before 1.1.5)
    assert vs.validate(ex("1.1.6")) == []   # spec 1.1.6 lists 1.1.6 (was the unlisted probe before 1.1.6)
    assert vs.validate(ex("1.1.7")) == []   # spec 1.1.7 lists 1.1.7 (was the unlisted probe before 1.1.7)
    assert vs.validate(ex("1.1.8")) == []   # spec 1.1.8 lists 1.1.8
    assert any(x.startswith("schema schema_version") for x in vs.validate(ex("1.1.9")))
