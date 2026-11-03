"""pytest: every test case in watcher_rules.yaml + alert contract (alert only, no order)."""
import pytest

import risk_reference as rr
import watcher_reference as wr

WR = wr.load_watcher_rules()
CASES = WR["tests"]
RULE_IDS = [r["id"] for r in WR["rules"]]


def _run(case):
    inp = rr.apply_case(WR["fixture"], case)
    return wr.poll(inp, case.get("state"), WR)


def _subset(actual, expected):
    for k, v in expected.items():
        if isinstance(v, dict):
            _subset(actual.get(k, {}), v)
        else:
            assert actual.get(k) == v, (k, actual.get(k), v)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_watcher_case(case):
    alerts, state, nxt = _run(case)
    assert [[a["rule_id"], a["level"]] for a in alerts] == case["expect"]["alerts"], [a["message"] for a in alerts]
    if "next_poll" in case["expect"]:
        assert nxt == case["expect"]["next_poll"]
    if "state" in case["expect"]:
        _subset(state, case["expect"]["state"])
    for a in alerts:  # alert contract
        for k in WR["alert_fields"]:
            assert k in a, k
        assert WR["config"]["alert_text"] in a["message"] and a["message"].endswith("alert only, no order")


def test_every_rule_has_2_or_more_cases():
    for rid in RULE_IDS:
        n = sum(1 for c in CASES if any(x[0] == rid for x in c["expect"]["alerts"]))
        assert n >= 2, rid


def test_replay_dedupes_and_escalates():
    """same warn twice -> one alert; then exit_signal -> sent; then exit again -> nothing."""
    case = next(c for c in CASES if c["id"] == "W01-a")
    a1, st, _ = _run(case)
    inp = rr.apply_case(WR["fixture"], {**case, "set": {**case["set"], "now": "2026-10-20T11:01:00-04:00"}})
    a2, st, _ = wr.poll(inp, st, WR)
    inp = rr.apply_case(WR["fixture"], {"set": {"now": "2026-10-20T11:02:00-04:00", "legs.0.bid.value": 3.55, "legs.0.ask.value": 3.65,
                                                 "legs.1.bid.value": 1.50, "legs.1.ask.value": 1.50}})
    a3, st, _ = wr.poll(inp, st, WR)
    a4, st, _ = wr.poll(inp, st, WR)
    assert [len(a1), len(a2), len(a3), len(a4)] == [1, 0, 1, 0]
    assert a3[0]["level"] == "exit_signal"


def test_watcher_never_orders():
    src = (wr.SPEC_DIR / "watcher_reference.py").read_text().lower()
    for word in ("place_order", "submit_order", "broker", "requests.", "urllib"):
        assert word not in src.replace("alert only, no order", ""), word
