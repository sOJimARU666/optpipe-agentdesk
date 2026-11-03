"""pytest: every test case in risk_rules.yaml + schema/example consistency."""
import json
from pathlib import Path

import pytest

import risk_reference as rr

SPEC = Path(__file__).resolve().parent
RULES = rr.load_rules()
CASES = RULES["tests"]


def _check_measured(actual, expected):
    for k, v in expected.items():
        assert actual is not None and k in actual, f"measured missing {k}: {actual}"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            assert actual[k] == pytest.approx(v, abs=0.01), (k, actual[k], v)
        else:
            assert actual[k] == v, (k, actual[k], v)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_rule_case(case):
    inp = rr.apply_case(RULES["fixture"], case)
    out = rr.evaluate(inp, RULES, case.get("config"), profile=case.get("profile"))
    if case["rule"] == "OVERALL":
        assert out["overall"]["verdict"] == case["expect"]["result"], out["overall"]
        assert out["overall"]["approval_required"] is True
        return
    r = next(x for x in out["results"] if x["rule_id"] == case["rule"])
    assert r["result"] == case["expect"]["result"], r
    if "measured" in case["expect"]:
        _check_measured(r["measured"], case["expect"]["measured"])


def test_every_rule_has_2_or_more_cases():
    ids = [r["id"] for r in RULES["rules"]]
    assert set(ids) == set(rr.RULE_FUNCS)
    for rid in ids:
        assert sum(c["rule"] == rid for c in CASES) >= 2, rid


def test_fixture_passes_everything():
    out = rr.evaluate(RULES["fixture"], RULES)
    assert [r["result"] for r in out["results"]] == ["PASS"] * len(RULES["rules"])
    assert out["overall"] == {"verdict": "PASS", "blocking_rule_ids": [], "flag_rule_ids": [], "approval_required": True}


def test_market_hours_ny_not_berlin():
    cal = rr.Calendar(RULES["config"]["market_hours"])
    # 2026-10-26: EU on CET (+1), US still EDT (-4) -> open is 14:30 Berlin
    assert cal.is_open(rr.ts("2026-10-26T14:30:00+01:00"))
    assert not cal.is_open(rr.ts("2026-10-26T14:29:00+01:00"))
    # normal week: open is 15:30 Berlin
    assert not cal.is_open(rr.ts("2026-10-19T15:29:00+02:00"))
    assert cal.is_open(rr.ts("2026-10-19T15:30:00+02:00"))
    # after US DST ends (Nov 2): back to 6h
    assert cal.is_open(rr.ts("2026-11-02T15:30:00+01:00"))
    assert not cal.is_open(rr.ts("2026-11-02T14:30:00+01:00"))
    # holiday & early close
    assert not cal.is_open(rr.ts("2026-11-26T11:00:00-05:00"))
    assert not cal.is_open(rr.ts("2026-11-27T13:30:00-05:00"))


def test_example_session_validates_and_reviews_match_reference():
    import validate_session
    sess = json.loads((SPEC / "session.example.json").read_text())
    errors = validate_session.validate(sess)
    assert errors == [], errors
    for rv in sess["risk_reviews"]:
        inp = rr.inputs_from_session(sess, rv["ticket_id"], rv["run_id"], rv["evaluated_at"]["ny"])
        out = rr.evaluate(inp, RULES)
        assert [(r["rule_id"], r["result"]) for r in out["results"]] == [(r["rule_id"], r["result"]) for r in rv["results"]]
        assert out["overall"]["verdict"] == rv["overall"]["verdict"]


# ---- validator negative tests (schema + invariants must actually bite) ----
import copy as _copy

def _example():
    import json as _json
    return _json.loads((Path(__file__).resolve().parent / "session.example.json").read_text())

def _mutations():
    def berlin_wrong_offset(s):  # Oct 13 Berlin is +02:00 (CEST); +01:00 is wrong
        s["session"]["created"]["berlin"] = s["session"]["created"]["berlin"].replace("+02:00", "+01:00")
    def unknown_source(s):
        s["candidates"][0]["snapshots"][0]["quote"]["last"]["source_id"] = "src-nope"
    def conflicting_with_value(s):
        s["global_snapshot"]["instruments"][2]["level"]["value"] = 5914.35
    def approval_on_veto(s):
        t = s["tickets"][0]
        s["approvals"].append({"approval_id": "ap-1", "ticket_id": t["ticket_id"], "review_id": "rv-postopen",
            "approver": {"name": "Eugene Ward", "channel": "web_ui"}, "approved_at": t["created"], "expires_at": s["session"]["updated"],
            "legs": t["legs"], "quantity": 1, "limit_price": t["limit_price"], "acknowledged_flags": [], "status": "active", "status_changed_at": t["created"]})
    def missing_no_trade(s):
        s["tickets"][0]["dm_alternatives"] = [a for a in s["tickets"][0]["dm_alternatives"] if a["kind"] != "no_trade"]
    def idea_status_mismatch(s):
        s["idea_log"][0]["current_status"] = "approved"
    def leg_not_in_chain(s):
        s["tickets"][0]["legs"][0]["contract_id"] = "JPM261030C00999000"
    def live_mode(s):
        s["session"]["mode"] = "live"
    return [berlin_wrong_offset, unknown_source, conflicting_with_value, approval_on_veto, missing_no_trade, idea_status_mismatch, leg_not_in_chain, live_mode]

# audit AUDIT-001B: each break must be caught by its own check, not by a side effect (error id prefix)
EXPECTED_PREFIX = {
    "berlin_wrong_offset": "INV-02 ",
    "unknown_source": "INV-01 ",
    "conflicting_with_value": "schema global_snapshot/instruments/2/level",
    "approval_on_veto": "INV-05-VETO ",          # 1.1.3 specific id (1.1.x: 'INV-05 ap-1 approves a VETO/foreign review')
    "missing_no_trade": "schema tickets/0/dm_alternatives",
    "idea_status_mismatch": "INV-07 ",
    "leg_not_in_chain": "INV-08 ",
    "live_mode": "schema session/mode",
}


@pytest.mark.parametrize("mut", _mutations(), ids=lambda f: f.__name__)
def test_validator_rejects(mut):
    import validate_session
    s = _copy.deepcopy(_example()); mut(s)
    errs = validate_session.validate(s)
    assert errs, f"{mut.__name__} was not caught"
    want = EXPECTED_PREFIX[mut.__name__]
    assert any(e.startswith(want) for e in errs), (want, errs)
