"""Watcher engine = Doodle's spec/watcher_rules.yaml (v1.1.0), loaded in place: every YAML case passes and
the output matches spec/watcher_reference.py exactly (parity), including state and next-poll interval."""
import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from optpipe.config import load_settings
from optpipe.risk.engine import Calendar
from optpipe.risk.ruleset import MarketHours
from optpipe.spec import discover
from optpipe.watcher.engine import QUOTE_RULES, SPECIAL_RULES, check_rules, poll
from optpipe.watcher.runtime import targets_from_session

_SPEC = discover(load_settings().spec_path)
_WR = _SPEC.watcher_rules()
CASES = (_WR or {}).get("tests", [])
pytestmark = pytest.mark.skipif(_WR is None, reason="Doodle's watcher_rules.yaml not found")


@pytest.fixture(scope="module")
def wref(spec):
    return spec.load_module("watcher_reference")


@pytest.fixture(scope="module")
def cal(raw_rules):
    return Calendar(MarketHours.model_validate(raw_rules["config"]["market_hours"]))


def _norm(o):
    return json.loads(json.dumps(o, default=str))


def test_rules_loaded_in_place_and_all_implemented(spec):
    assert spec.path("watcher_rules").parent == spec.root
    check_rules(_WR)
    assert {r["id"] for r in _WR["rules"]} == set(QUOTE_RULES) | SPECIAL_RULES
    assert _WR["config"]["alert_text"] == "alert only, no order"
    assert len(CASES) >= 59 and {f"SCOPE-{x}" for x in "efghijklmnopqr"} <= {c["id"] for c in CASES}


@pytest.fixture(scope="module")
def session_fx(spec):
    """Doodle's spec/session_fixtures.py (imports risk_reference by name, so spec/ goes on sys.path while loading)."""
    import sys
    sys.path.insert(0, str(spec.root))
    try:
        import session_fixtures
        yield session_fixtures
    finally:
        sys.path.remove(str(spec.root))


SESSION_NOW = datetime(2026, 10, 13, 13, 0, tzinfo=ZoneInfo("America/New_York"))   # inside ap-1's window (YAML)


SESSION_KEYS = ("approval_status", "review_tamper", "idea_status")     # spec 1.1.5 test_watcher_reference.SESSION_KEYS


def _case_session(fx, approval_status: str = "active", review_tamper=None, idea_status=None) -> dict:
    """The YAML session keys exactly as Doodle's test_watcher_reference._session_target builds them:
    session_fixtures.approved_record without a position; approval ap-1 set to `approval_status` (status_changed_at
    12:40; `none` removes it); spec 1.1.5 `review_tamper` makes ap-1's review rv-flag fail INV-16
    (session_fixtures.tamper_review); `idea_status` sets the ticket's idea current_status (history entry at 12:45)."""
    s = fx.approved_record(with_position=False)
    a = next(x for x in s["approvals"] if x["approval_id"] == "ap-1")
    if approval_status == "none":
        s["approvals"].remove(a)
    elif approval_status != "active":
        a["status"], a["status_changed_at"] = approval_status, fx.at("12:40")
    if review_tamper:
        fx.tamper_review(next(r for r in s["risk_reviews"] if r["review_id"] == "rv-flag"), review_tamper)
    if idea_status:
        idea = next(i for i in s["idea_log"] if i["ticket_id"] == fx.TK)
        idea["history"].append({"status": idea_status, "at": fx.at("12:45"), "actor": "user", "reason": "test fixture"})
        idea["current_status"] = idea_status
    return s


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_yaml_case(case, ref, wref, cal, session_fx):
    if any(k in case for k in SESSION_KEYS):            # SCOPE-e..r: the target comes from a session record
        s = _case_session(session_fx, case.get("approval_status", "active"), case.get("review_tamper"),
                          case.get("idea_status"))
        t = next(t for t in targets_from_session(s, SESSION_NOW) if t.kind == "ticket" and t.ticket_id == session_fx.TK)
        approved = t.status == "approved_pending" and not t.hypothetical
        assert t.status == case["expect"].get("target_status", t.status), t.note
        assert approved is case["expect"].get("target_approved", approved), t.note
        theirs = wref.target_from_session(s, session_fx.TK, now=SESSION_NOW.isoformat())
        assert (t.status, t.vetoed, approved) == (theirs["status"], theirs["status"] == "vetoed", theirs["approved"])
        status = t.status
        case = {**case, "set": {**case.get("set", {}),
                                "target": {"kind": "ticket", "id": "tk-1", "ticket_id": "tk-1", "status": status}}}
    inp = ref.apply_case(_WR["fixture"], case)
    alerts, state, nxt = poll(inp, case.get("state"), _WR, cal)
    assert [[a["rule_id"], a["level"]] for a in alerts] == case["expect"]["alerts"], [a["message"] for a in alerts]
    if "next_poll" in case["expect"]:
        assert nxt == case["expect"]["next_poll"]
    for k, v in (case["expect"].get("state") or {}).items():
        for kk, vv in v.items():
            assert state[k][kk] == vv
    for a in alerts:
        assert all(k in a for k in _WR["alert_fields"])
        assert a["message"].endswith("alert only, no order")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_parity_with_reference(case, ref, wref, cal):
    inp = ref.apply_case(_WR["fixture"], case)
    snap = copy.deepcopy(inp)
    want = wref.poll(copy.deepcopy(inp), copy.deepcopy(case.get("state")), _WR)
    got = poll(inp, case.get("state"), _WR, cal)
    assert _norm(got) == _norm(want)
    assert inp == snap, "poll must not mutate its input"


def test_replay_dedupes_and_escalates(ref, cal):
    case = next(c for c in CASES if c["id"] == "W01-a")
    a1, st, _ = poll(ref.apply_case(_WR["fixture"], case), None, _WR, cal)
    a2, st, _ = poll(ref.apply_case(_WR["fixture"], {**case, "set": {**case["set"], "now": "2026-10-20T11:01:00-04:00"}}),
                     st, _WR, cal)
    inp = ref.apply_case(_WR["fixture"], {"set": {"now": "2026-10-20T11:02:00-04:00", "legs.0.bid.value": 3.55,
                                                  "legs.0.ask.value": 3.65, "legs.1.bid.value": 1.50, "legs.1.ask.value": 1.50}})
    a3, st, _ = poll(inp, st, _WR, cal)
    a4, st, _ = poll(inp, st, _WR, cal)
    assert [len(a1), len(a2), len(a3), len(a4)] == [1, 0, 1, 0] and a3[0]["level"] == "exit_signal"


def test_unknown_rule_fails_loudly():
    wr = copy.deepcopy(_WR)
    wr["rules"].append({"id": "W99_NEW"})
    with pytest.raises(NotImplementedError):
        check_rules(wr)
