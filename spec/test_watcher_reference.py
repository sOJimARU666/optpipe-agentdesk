"""pytest: every test case in watcher_rules.yaml + alert contract (alert only, no order)."""
import pytest

import risk_reference as rr
import session_fixtures as fx
import watcher_reference as wr

WR = wr.load_watcher_rules()
CASES = WR["tests"]
RULE_IDS = [r["id"] for r in WR["rules"]]


SESSION_NOW = "2026-10-13T13:00:00-04:00"   # inside session_fixtures approval ap-1's window (12:25-16:00 NY)


def _session_target(approval_status, review_tamper=None, idea_status=None):
    """spec 1.1.4: build the ticket target from a session record whose approval ap-1 has `approval_status`
    ('none' = no approval) via target_from_session, the way a watcher resolves it.
    spec 1.1.5: `review_tamper` (session_fixtures.TAMPER_KINDS) makes ap-1's review rv-flag fail INV-16;
    `idea_status` sets the ticket's idea current_status (default: the fixture's 'approved')."""
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
    return wr.target_from_session(s, fx.TK, now=SESSION_NOW)


SESSION_KEYS = ("approval_status", "review_tamper", "idea_status")


def _run(case):
    if any(k in case for k in SESSION_KEYS):
        t = _session_target(case.get("approval_status", "active"), case.get("review_tamper"), case.get("idea_status"))
        if "target_status" in case["expect"]:
            assert t["status"] == case["expect"]["target_status"], t
        if "target_approved" in case["expect"]:
            assert t["approved"] is case["expect"]["target_approved"], t
        tgt = {"kind": "ticket", "id": "tk-1", "ticket_id": "tk-1", "status": t["status"]}
        case = {**case, "set": {**case.get("set", {}), "target": tgt}}
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


# ------------------------------------------------- spec 1.1.4: approval status -> ticket target
_EXPECT = {"real": ("approved_pending", True, False), "hypothetical": ("approved_pending", False, True),
           "rejected": ("rejected", False, True)}


def test_approval_status_mapping_is_complete_and_matches_reference():
    m = WR["scope"]["approval_status"]
    assert set(m) == {"active", "revoked", "expired", "consumed", "none"}
    assert m == wr.APPROVAL_STATUS_TARGET
    assert m["revoked"] == "rejected"   # spec 1.1.4 ruling (fail closed, matches optpipe)


@pytest.mark.parametrize("status", ["active", "revoked", "expired", "consumed", "none"])
def test_approval_status_mapping_drives_target_from_session(status):
    t = _session_target(status)
    assert (t["status"], t["approved"], t["hypothetical"]) == _EXPECT[WR["scope"]["approval_status"][status]], t
    assert t["approval_id"] == ("ap-1" if status == "active" else None)


def test_approval_status_cases_cover_revoked():
    ids = {c["id"]: c for c in CASES if "approval_status" in c}
    assert {"SCOPE-e", "SCOPE-f", "SCOPE-g"} <= set(ids)
    assert ids["SCOPE-e"]["approval_status"] == "revoked" and ids["SCOPE-e"]["expect"]["target_status"] == "rejected"


# ------------------------------------------------------------------ spec 1.1.5 rulings
def test_target_precedence_matches_reference():
    assert tuple(WR["scope"]["target_precedence"]) == wr.TARGET_PRECEDENCE
    assert wr.TARGET_PRECEDENCE == ("vetoed", "terminal", "rejected", "approved_pending", "idea_status")
    assert wr.TERMINAL_IDEA_STATUSES == ("executed", "closed")
    assert wr.APPROVED_IDEA_STATUSES == ("approved_pending", "approved")


@pytest.mark.parametrize("kind", fx.TAMPER_KINDS)
@pytest.mark.parametrize("status", ["active", "revoked", "expired", "consumed"])
def test_inv16_inconsistent_review_is_veto_for_the_watcher(kind, status):
    """ruling 1: an approval whose review fails INV-16 never counts as really approved: the ticket is vetoed (VETO
    first, before a revoked approval or anything else), whatever the approval status."""
    t = _session_target(status, review_tamper=kind)
    assert (t["status"], t["approved"], t["hypothetical"], t["approval_id"]) == ("vetoed", False, True, None), t


@pytest.mark.parametrize("kind", fx.TAMPER_KINDS)
def test_real_approval_rejects_inv16_inconsistent_review(kind):
    s = fx.approved_record(with_position=False)
    assert wr.real_approval(s, fx.TK, SESSION_NOW)["approval_id"] == "ap-1"
    fx.tamper_review(next(r for r in s["risk_reviews"] if r["review_id"] == "rv-flag"), kind)
    assert wr.real_approval(s, fx.TK, SESSION_NOW) is None


def test_review_blocked_reuses_validator_inv16():
    import validate_session as vs
    rv = fx.flag_review(fx.example())
    assert not wr.review_blocked(rv) and not wr.review_blocked(None)
    for kind in fx.TAMPER_KINDS:
        bad = fx.tamper_review(fx.flag_review(fx.example()), kind)
        assert vs.overall_errors(bad) and wr.review_blocked(bad), kind


def test_inv16_veto_ignores_the_version_label():
    """the watcher fails closed on a tampered record of ANY schema_version (INV-16 is a 1.1.3+ record error only)."""
    for v in ("1.0.0", "1.1.0", "1.1.2", "1.1.3", "1.1.4", "1.1.5", "1.1.6", "1.1.7", "1.1.8"):
        s = fx.approved_record(with_position=False); s["schema_version"] = v
        fx.tamper_review(next(r for r in s["risk_reviews"] if r["review_id"] == "rv-flag"), "pass_over_flag")
        assert wr.target_from_session(s, fx.TK, now=SESSION_NOW)["status"] == "vetoed", v


IDEA_STATUSES = ["proposed", "approved_pending", "approved", "simulated", "executed", "closed", None]


def _with_idea(s, idea_status):
    idea = next(i for i in s["idea_log"] if i["ticket_id"] == fx.TK)
    if idea_status is None:
        s["idea_log"].remove(idea)
    else:
        idea["current_status"] = idea_status
    return s


def _revoked(s):
    a = next(x for x in s["approvals"] if x["approval_id"] == "ap-1")
    a["status"], a["status_changed_at"] = "revoked", fx.at("12:40")
    return s


@pytest.mark.parametrize("idea_status", ["proposed", "approved_pending", "approved", "simulated", "rejected", None])
def test_revoked_approval_rejects_every_pre_execution_idea(idea_status):
    """precedence step 3: a revoked approval (and no real approval) maps a pre-execution ticket to rejected, so it
    overrides step 4 for proposed and simulated ideas."""
    s = _with_idea(_revoked(fx.approved_record(with_position=False)), idea_status)
    t = wr.target_from_session(s, fx.TK, now=SESSION_NOW)
    assert (t["status"], t["approved"], t["hypothetical"]) == ("rejected", False, True), t


@pytest.mark.parametrize("idea_status", ["executed", "closed"])
@pytest.mark.parametrize("approval_status", ["revoked", "active", "expired", "consumed"])
def test_terminal_idea_keeps_its_status(idea_status, approval_status):
    """precedence step 2: an executed / closed idea keeps its status; a revoked approval does not relabel it (the trade
    is watched by the position rules), and no approval makes it a watched ticket."""
    s = _with_idea(fx.approved_record(with_position=False), idea_status)
    a = next(x for x in s["approvals"] if x["approval_id"] == "ap-1")
    if approval_status != "active":
        a["status"], a["status_changed_at"] = approval_status, fx.at("12:40")
    t = wr.target_from_session(s, fx.TK, now=SESSION_NOW)
    assert (t["status"], t["approved"], t["hypothetical"], t["approval_id"]) == (idea_status, False, True, None), t


def test_terminal_idea_still_loses_to_veto():
    s = _with_idea(_revoked(fx.approved_record(with_position=False)), "executed")
    fx.tamper_review(next(r for r in s["risk_reviews"] if r["review_id"] == "rv-flag"), "pass_over_flag")
    assert wr.target_from_session(s, fx.TK, now=SESSION_NOW)["status"] == "vetoed"


@pytest.mark.parametrize("idea_status", ["proposed", "simulated", "executed", "closed", "rejected", "vetoed", None])
def test_active_approval_on_a_non_approved_idea_is_never_real(idea_status):
    """precedence step 5: an active, in-window, INV-05 / INV-16 clean approval is real ONLY when the idea is
    approved_pending or approved."""
    s = fx.approved_record(with_position=False)
    assert wr.real_approval(s, fx.TK, SESSION_NOW)["approval_id"] == "ap-1"     # approved_pending idea: real
    _with_idea(s, idea_status)
    assert wr.real_approval(s, fx.TK, SESSION_NOW) is None
    assert wr.target_from_session(s, fx.TK, now=SESSION_NOW)["approved"] is False


@pytest.mark.parametrize("idea_status", ["approved_pending", "approved"])
def test_active_approval_on_an_approved_idea_is_real(idea_status):
    s = _with_idea(fx.approved_record(with_position=False), idea_status)
    t = wr.target_from_session(s, fx.TK, now=SESSION_NOW)
    assert (t["status"], t["approved"], t["approval_id"]) == ("approved_pending", True, "ap-1"), t


def test_active_approval_with_no_idea_is_watched_but_hypothetical():
    """interpretation of step 5: a ticket with no idea_log entry has no approved idea, so even an active approval is not
    real; the ticket is still watched (step 4) as a hypothetical what-if."""
    t = wr.target_from_session(_with_idea(fx.approved_record(with_position=False), None), fx.TK, now=SESSION_NOW)
    assert (t["status"], t["approved"], t["hypothetical"]) == ("approved_pending", False, True), t


def test_revoked_loses_to_veto_and_to_a_real_approval_on_an_approved_idea():
    s = fx.approved_record(with_position=False)
    s["approvals"].append(fx.approval(s, approval_id="ap-0", status="revoked", status_changed_at=fx.at("12:40")))
    t = wr.target_from_session(s, fx.TK, now=SESSION_NOW)
    assert (t["status"], t["approved"], t["approval_id"]) == ("approved_pending", True, "ap-1"), t   # real wins (1.1.4)
    idea = next(i for i in s["idea_log"] if i["ticket_id"] == fx.TK); idea["current_status"] = "proposed"
    t = wr.target_from_session(s, fx.TK, now=SESSION_NOW)
    assert (t["status"], t["approved"]) == ("rejected", False), t   # ap-1 not real on a proposed idea -> revoked wins
    idea["current_status"] = "executed"
    assert wr.target_from_session(s, fx.TK, now=SESSION_NOW)["status"] == "executed"   # terminal keeps its status
    idea["current_status"] = "vetoed"
    assert wr.target_from_session(s, fx.TK, now=SESSION_NOW)["status"] == "vetoed"     # VETO first
    idea["current_status"] = "rejected"
    assert wr.target_from_session(s, fx.TK, now=SESSION_NOW)["status"] == "rejected"   # idea rejected always


def test_1_1_5_cases_present():
    ids = {c["id"]: c for c in CASES}
    for cid, tamper, idea, status in (("SCOPE-h", "pass_over_veto", None, "vetoed"),
                                      ("SCOPE-i", "pass_over_flag", None, "vetoed"),
                                      ("SCOPE-j", "pass_over_flag", None, "vetoed"),
                                      ("SCOPE-k", "flag_ids_mismatch", None, "vetoed"),
                                      ("SCOPE-l", None, "proposed", "rejected"),
                                      ("SCOPE-m", None, "proposed", "rejected"),
                                      ("SCOPE-n", None, "proposed", "proposed"),
                                      ("SCOPE-o", None, "executed", "executed"),
                                      ("SCOPE-p", None, "executed", "executed"),
                                      ("SCOPE-q", None, "simulated", "rejected"),
                                      ("SCOPE-r", None, "approved", "approved_pending")):
        c = ids[cid]
        assert c.get("review_tamper") == tamper and c.get("idea_status") == idea, cid
        assert c["expect"]["target_status"] == status, cid
