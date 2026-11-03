"""Spec 1.1.3 (FIX-SEC-001): INV-03/04 tightening, INV-05 flag acknowledgement, INV-06 approval status/ticket/terms,
INV-16 overall-verdict consistency, combine() precedence, fail-closed CLI, and back-compat with the frozen 1.1.2 validator.
pytest-only (no new YAML cases: risk_rules.yaml / watcher_rules.yaml case counts are unchanged). PAPER ONLY.
Spec 1.1.4: hermetic back-compat records (vendored under fixtures/, resolved relative to this file; a missing fixture
FAILS), revoked approval -> watcher target 'rejected', VERSION / labels 1.1.4 (see test_release_v1_1_4.py)."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import risk_reference as rr
import session_fixtures as fx
import validate_session as vs
import watcher_reference as wr

SPEC = Path(__file__).resolve().parent
PY = sys.executable
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def errs(s):
    return vs.validate(s)


def has(es, prefix):
    return any(e.startswith(prefix) for e in es)


def ids(es):
    return sorted({e.split(" ", 1)[0] for e in es})


def rec():
    return fx.approved_record()


def pos(s):
    return next(p for p in s["paper_positions"] if p["position_id"] == "pos-1")


def appr(s, aid="ap-1"):
    return next(a for a in s["approvals"] if a["approval_id"] == aid)


def rvw(s, rid="rv-flag"):
    return next(r for r in s["risk_reviews"] if r["review_id"] == rid)


def tk(s):
    return s["tickets"][0]


# ------------------------------------------------------------------ fixtures sanity
def test_example_is_1_1_6_and_valid():
    s = fx.example()
    assert s["schema_version"] == "1.1.6" and errs(s) == []
    assert (SPEC / "VERSION").read_text().strip() == "1.1.6"


def test_approved_record_is_valid_positive_control():
    assert errs(rec()) == []
    assert errs(fx.approved_record(with_position=False)) == []


# ------------------------------------------------------------------ AUDIT-001B: 15 INV negative tests
def test_inv03_duplicate_source_id():
    s = rec(); s["sources"].append(copy.deepcopy(s["sources"][0]))
    assert has(errs(s), "INV-03 duplicate source id")


def test_inv03_duplicate_leg_id():
    s = rec(); t = tk(s); t["legs"][1]["leg_id"] = t["legs"][0]["leg_id"]
    assert has(errs(s), "INV-03-LEG ")


def test_inv03_duplicate_contract_id():
    s = rec(); sl = s["candidates"][0]["snapshots"][-1]["option_chain_slices"][0]
    sl["contracts"].append(copy.deepcopy(sl["contracts"][0]))
    assert has(errs(s), "INV-03-CONTRACT ")


def test_inv04_unresolved_ticket_candidate_id():
    s = rec(); tk(s)["candidate_id"] = "cand-nope"
    assert has(errs(s), "INV-04 ticket.candidate_id -> cand-nope unresolved")


def test_inv04_unresolved_review_ticket_id():
    s = rec(); rvw(s)["ticket_id"] = "tkt-nope"
    assert has(errs(s), "INV-04 review.ticket_id -> tkt-nope unresolved")


def test_inv04_unresolved_fill_leg_id():
    s = rec(); pos(s)["fills"][0]["leg_id"] = "leg-9"
    assert has(errs(s), "INV-04-FILL-LEG f-1 ")


def test_inv06_fill_before_approval():
    s = rec(); pos(s)["fills"][0]["filled_at"] = fx.at("12:24")
    assert has(errs(s), "INV-06-BEFORE f-1 ")


def test_inv06_fill_after_expiry():
    s = rec(); pos(s)["fills"][0]["filled_at"] = fx.at("16:01")
    assert has(errs(s), "INV-06-EXPIRED f-1 ")


def test_inv06_debit_above_limit():
    s = rec(); pos(s)["fills"][0]["price"] = 4.06          # 4.06 - 0.95 = 3.11 > 3.10
    assert has(errs(s), "INV-06-LIMIT pos-1 ")
    s = rec(); pos(s)["fills"][0]["price"] = 4.05          # exactly 3.10: Decimal, no float slack needed
    assert not has(errs(s), "INV-06-LIMIT")


def _credit_case(sell, buy):
    """bear call credit: sell leg-1, buy leg-2, min credit 1.00 (function level; same INV-06 code path)."""
    s = rec(); t = copy.deepcopy(tk(s))
    t["legs"][0]["side"], t["legs"][1]["side"] = "sell", "buy"
    t["limit_price"] = {**t["limit_price"], "price_type": "credit", "amount": 1.0}
    a = fx.approval(s, legs=copy.deepcopy(t["legs"]), limit_price=copy.deepcopy(t["limit_price"]))
    p = fx.position(fills=[fx.fill("f-1", "leg-1", "JPM261030C00340000", "sell_to_open", sell),
                           fx.fill("f-2", "leg-2", "JPM261030C00350000", "buy_to_open", buy)])
    return vs.position_errors(p, a, t, {r["review_id"]: r for r in s["risk_reviews"]})


def test_inv06_credit_below_limit():
    assert has(_credit_case(2.00, 1.20), "INV-06-LIMIT pos-1 ")     # credit 0.80 < 1.00
    assert _credit_case(2.20, 1.20) == []                            # credit 1.00 == limit: ok


def test_inv06_revoked_approval():
    s = rec(); a = appr(s); a["status"], a["status_changed_at"] = "revoked", fx.at("12:28")
    assert has(errs(s), "INV-06-STATUS f-1 ")


def test_inv06_position_ticket_mismatch():
    s = rec(); other = copy.deepcopy(tk(s)); other["ticket_id"] = "tkt-other"; s["tickets"].append(other)
    pos(s)["ticket_id"] = "tkt-other"
    assert has(errs(s), "INV-06-TICKET pos-1 ")


def _src(s, sid):
    return next(x for x in s["sources"] if x["id"] == sid)


def test_inv09_as_of_after_fetch():
    s = rec(); q = s["candidates"][0]["snapshots"][0]["quote"]["last"]
    q["as_of"] = "2026-10-13T07:53:00-04:00"                        # source fetched 07:52
    assert has(errs(s), "INV-09 ")


def test_inv09_boundary_equal_passes():
    s = rec(); q = s["candidates"][0]["snapshots"][0]["quote"]["last"]
    q["as_of"] = _src(s, q["source_id"])["fetched_at"]
    assert not has(errs(s), "INV-09")


def test_inv09_missing_status_ignored():
    s = rec(); q = s["candidates"][0]["snapshots"][0]["quote"]["last"]
    q.update(value=None, status="missing", as_of="2026-10-13T09:00:00-04:00")   # after fetch, but not a usable value
    e = errs(s)
    assert not has(e, "INV-09"), e


# ------------------------------------------------------------------ INV-03 / INV-04 tightening (1.1.3 only)
def test_inv03_duplicate_fill_id():
    s = rec(); pos(s)["fills"][1]["fill_id"] = "f-1"
    assert has(errs(s), "INV-03-FILL duplicate fill_id f-1")


def test_inv04_fill_contract_mismatch():
    s = rec(); pos(s)["fills"][0]["contract_id"] = "JPM261030C00350000"
    assert has(errs(s), "INV-04-FILL-CONTRACT f-1 ")


def test_inv04_mark_leg_unresolved():
    s = rec(); p = pos(s)
    nf = lambda v: {"value": v, "source_id": "src-nasdaq-jpm-chain-1216", "as_of": "2026-10-13T12:16:00-04:00", "status": "ok"}
    mark = {"marked_at": fx.at("12:45"), "underlying_last": nf(337.0), "position_value": nf(3.0), "unrealized_pnl_usd": nf(-5.0),
            "leg_marks": [{"leg_id": "leg-1", "mid": nf(4.0)}, {"leg_id": "leg-2", "mid": nf(1.0)}]}
    p["marks"] = [copy.deepcopy(mark)]
    assert errs(s) == []
    del p["marks"][0]["leg_marks"]                          # leg_marks is optional: must not crash
    assert errs(s) == []
    p["marks"] = [mark]; mark["leg_marks"][1]["leg_id"] = "leg-9"
    assert has(errs(s), "INV-04-MARK-LEG pos-1 ")


# ------------------------------------------------------------------ INV-05 (1.1.3): flag acknowledgement + ids
def test_flag_approval_no_ack():
    s = rec(); appr(s)["acknowledged_flags"] = []
    e = errs(s)
    assert has(e, "INV-05-ACK-MISSING ap-1 ") and ids(e) == ["INV-05-ACK-MISSING"], e


def test_flag_approval_absent_ack_field():
    s = rec(); del appr(s)["acknowledged_flags"]
    assert has(errs(s), "INV-05-ACK-MISSING ap-1 ")


def test_flag_approval_partial_ack():
    s = rec(); appr(s)["acknowledged_flags"] = fx.FLAGS[:2]
    e = errs(s)
    assert has(e, "INV-05-ACK-MISSING ap-1 ") and "R11_CONCENTRATION" in " ".join(e)


def test_flag_approval_extra_ack():
    s = rec(); appr(s)["acknowledged_flags"] = fx.FLAGS + ["R02_BID_ASK_WIDTH"]
    assert has(errs(s), "INV-05-ACK-EXTRA ap-1 ")


def test_flag_approval_duplicate_ack():
    s = rec(); appr(s)["acknowledged_flags"] = fx.FLAGS + [fx.FLAGS[0]]
    e = errs(s)
    assert has(e, "INV-05-ACK-DUPLICATE ap-1 ") and not has(e, "INV-05-ACK-MISSING"), e


def test_flag_approval_full_ack_ok_any_order():
    s = rec(); appr(s)["acknowledged_flags"] = list(reversed(fx.FLAGS))
    assert errs(s) == []


def test_pass_review_ack_must_be_empty():
    s = rec(); rv = rvw(s)
    for r in rv["results"]:
        r["result"] = "PASS"
    rv["overall"] = {**rr.overall(rv["results"]), "summary": "pass"}
    appr(s)["acknowledged_flags"] = []
    assert errs(s) == []
    appr(s)["acknowledged_flags"] = ["R11_CONCENTRATION"]
    assert has(errs(s), "INV-05-ACK-EXTRA ap-1 ")


def test_approval_on_veto_review_specific_id():
    s = rec(); appr(s)["review_id"] = "rv-postopen"
    e = errs(s)
    assert has(e, "INV-05-VETO ap-1 ") and not has(e, "INV-05-ACK"), e


def test_approval_on_review_whose_results_give_veto():
    s = rec(); rv = rvw(s); rv["results"][1]["result"] = "VETO"   # stored overall still FLAG
    e = errs(s)
    assert has(e, "INV-05-VETO ap-1 ") and has(e, "INV-16-VERDICT rv-flag ")


def test_approval_foreign_terms_expiry():
    s = rec(); rv = copy.deepcopy(rvw(s)); rv["review_id"] = "rv-other"; rv["ticket_id"] = "tkt-other"
    other = copy.deepcopy(tk(s)); other["ticket_id"] = "tkt-other"; s["tickets"].append(other); s["risk_reviews"].append(rv)
    appr(s)["review_id"] = "rv-other"
    assert has(errs(s), "INV-05-FOREIGN ap-1 ")
    s = rec(); appr(s)["quantity"] = 2
    assert has(errs(s), "INV-05-TERMS ap-1 ")
    s = rec(); appr(s)["expires_at"] = fx.at("12:25")
    assert has(errs(s), "INV-05-EXPIRY ap-1 ")


# ------------------------------------------------------------------ INV-06 (1.1.3): status / status_changed_at
@pytest.mark.parametrize("status", ["revoked", "expired", "consumed"])
@pytest.mark.parametrize("changed,bad", [("12:28", True), ("12:30", True), ("12:31", False)])
def test_inv06_nonactive_status_vs_fill_time(status, changed, bad):
    """fills at 12:30: status changed at or before the fill -> INV-06-STATUS; strictly after -> ok."""
    s = rec(); a = appr(s); a["status"], a["status_changed_at"] = status, fx.at(changed)
    e = errs(s)
    assert has(e, "INV-06-STATUS f-") == bad, e
    if not bad:
        assert e == []


@pytest.mark.parametrize("status", ["revoked", "expired", "consumed"])
def test_inv06_nonactive_without_status_changed_at(status):
    s = rec(); a = appr(s); a["status"] = status; a.pop("status_changed_at", None)
    assert has(errs(s), "INV-06-STATUS-UNDATED pos-1 ")


def test_inv06_closing_fill_after_consumed_ok():
    s = rec(); a = appr(s); a["status"], a["status_changed_at"] = "consumed", fx.at("12:31")
    p = pos(s); p["status"] = "closed"
    p["fills"] += [fx.fill("f-3", "leg-1", "JPM261030C00340000", "sell_to_close", 4.5, when="15:00"),
                   fx.fill("f-4", "leg-2", "JPM261030C00350000", "buy_to_close", 1.0, when="15:00")]
    e = errs(s)
    assert not has(e, "INV-06"), e


def test_inv06_fill_at_expiry_boundary_ok():
    """1.1.3-1.1.5 records keep the closed window (a fill exactly at expires_at is accepted)"""
    s = rec(); s["schema_version"] = "1.1.5"; [f.update(filled_at=fx.at("16:00")) for f in pos(s)["fills"]]
    assert not has(errs(s), "INV-06")


def test_inv06_fill_at_expiry_boundary_invalid_from_1_1_6():
    """spec 1.1.6 (SOL-ACCEPT-001 G4): valid only while filled_at < expires_at; more in test_release_v1_1_6.py"""
    s = rec(); assert s["schema_version"] == "1.1.6"; [f.update(filled_at=fx.at("16:00")) for f in pos(s)["fills"]]
    assert has(errs(s), "INV-06-EXPIRED f-1 ")


def test_inv06_terms_veto_qty_side():
    s = rec(); appr(s)["review_id"] = "rv-postopen"
    assert has(errs(s), "INV-06-VETO pos-1 ")
    s = rec(); appr(s)["limit_price"] = {**appr(s)["limit_price"], "amount": 3.2}
    assert has(errs(s), "INV-06-TERMS pos-1 ")
    s = rec(); pos(s)["fills"][0]["qty"] = 2
    assert has(errs(s), "INV-06-QTY pos-1 ")
    s = rec(); pos(s)["fills"][0]["action"] = "sell_to_open"
    assert has(errs(s), "INV-06-QTY f-1 ")


# ------------------------------------------------------------------ INV-16 (1.1.3): overall consistency
def test_overall_pass_hides_veto():
    s = fx.example(); rv = s["risk_reviews"][1]
    rv["overall"].update(verdict="PASS", blocking_rule_ids=[])
    assert has(errs(s), "INV-16-VERDICT rv-postopen overall.verdict PASS but rule results give VETO")


def test_overall_pass_hides_hard_unknown():
    s = rec(); rv = rvw(s)
    r01 = next(r for r in rv["results"] if r["severity"] == "hard"); r01["result"] = "UNKNOWN"
    e = errs(s)
    assert has(e, "INV-16-VERDICT rv-flag overall.verdict FLAG but rule results give VETO"), e
    rv["overall"]["verdict"] = "PASS"; rv["overall"]["flag_rule_ids"] = []
    assert has(errs(s), "INV-16-VERDICT rv-flag overall.verdict PASS but rule results give VETO")


def test_overall_flag_hides_veto():
    s = fx.example(); rv = s["risk_reviews"][1]
    rv["overall"].update(verdict="FLAG", blocking_rule_ids=[])
    assert has(errs(s), "INV-16-VERDICT rv-postopen ")


def test_blocking_and_flag_ids_mismatch():
    s = fx.example(); rv = s["risk_reviews"][1]
    rv["overall"]["blocking_rule_ids"] = rv["overall"]["blocking_rule_ids"][:-1]
    assert has(errs(s), "INV-16-BLOCKING rv-postopen ")
    s = rec(); rvw(s)["overall"]["flag_rule_ids"] = fx.FLAGS[:2]
    assert has(errs(s), "INV-16-FLAGS rv-flag ")
    s = rec(); rvw(s)["overall"]["flag_rule_ids"] = fx.FLAGS + [fx.FLAGS[0]]
    assert has(errs(s), "INV-16-FLAGS rv-flag ")


def test_overall_order_insensitive():
    s = rec(); rvw(s)["overall"]["flag_rule_ids"] = list(reversed(fx.FLAGS))
    assert errs(s) == []


def test_zero_results_fail_closed():
    rv = copy.deepcopy(rvw(rec())); rv["results"] = []
    assert ids(vs.overall_errors(rv)) == ["INV-16-EMPTY"]
    assert vs.review_is_veto(rv)
    s = rec(); rvw(s)["results"] = []
    assert has(errs(s), "schema risk_reviews/")   # results minItems 1 already rejects it in every version


# ------------------------------------------------------------------ combine / overall precedence
@pytest.mark.parametrize("results,want", [
    ([], "UNKNOWN"),                                  # spec 1.1.3: fail closed (was PASS)
    (["PASS"], "PASS"), (["PASS", "FLAG"], "FLAG"),
    (["FLAG", "UNKNOWN"], "UNKNOWN"), (["UNKNOWN", "FLAG"], "UNKNOWN"),   # UNKNOWN beats FLAG
    (["UNKNOWN", "VETO", "FLAG"], "VETO"), (["PASS", "UNKNOWN"], "UNKNOWN"),
])
def test_combine_precedence(results, want):
    assert rr.combine(results) == want
    assert rr.combine(iter(results)) == want


def test_combine_empty_not_pass():
    assert rr.combine([]) != "PASS"


def _r(rid, sev, result):
    return {"rule_id": rid, "severity": sev, "result": result}


def test_overall_precedence():
    assert rr.overall([_r("R11_X", "soft", "FLAG")])["verdict"] == "FLAG"
    assert rr.overall([_r("R01_X", "hard", "UNKNOWN"), _r("R11_X", "soft", "FLAG")])["verdict"] == "VETO"
    o = rr.overall([_r("R08_X", "soft", "UNKNOWN"), _r("R11_X", "soft", "FLAG")])
    assert o["verdict"] == "FLAG" and o["flag_rule_ids"] == ["R08_X", "R11_X"]
    assert rr.overall([_r("R11_X", "soft", "VETO"), _r("R12_X", "hard", "PASS")])["verdict"] == "VETO"


def test_r08_two_macro_paths_unknown_beats_flag():
    """R08-style: one window event FLAG + another path UNKNOWN combine to UNKNOWN."""
    assert rr.combine(["PASS", "FLAG", "UNKNOWN"]) == "UNKNOWN"


# ------------------------------------------------------------------ schema (all versions)
def test_schema_approval_required_false_rejected():
    s = fx.example(); s["risk_reviews"][0]["overall"]["approval_required"] = False
    assert has(errs(s), "schema risk_reviews/0/overall/approval_required")


def test_schema_verdict_override_rejected():
    s = fx.example(); s["risk_reviews"][0]["overall"]["verdict_override"] = "PASS"
    assert has(errs(s), "schema risk_reviews/0/overall")


def test_schema_decision_field_rejected():
    s = rec(); appr(s)["decision"] = "approved"
    assert has(errs(s), "schema approvals/")


# ------------------------------------------------------------------ watcher target (status only)
def test_watcher_active_acknowledged_approval_is_real():
    t = wr.target_from_session(rec(), fx.TK, now="2026-10-13T13:00:00-04:00")
    assert t["status"] == "approved_pending" and t["approved"] and not t["hypothetical"] and t["approval_id"] == "ap-1"


def test_watcher_ignores_decision_field():
    s = rec(); a = appr(s); a["status"], a["status_changed_at"], a["decision"] = "revoked", fx.at("12:40"), "approved"
    t = wr.target_from_session(s, fx.TK, now="2026-10-13T13:00:00-04:00")
    assert t["status"] == "rejected" and not t["approved"] and t["hypothetical"]   # 1.1.4: revoked -> rejected
    src = (SPEC / "watcher_reference.py").read_text()
    assert 'get("decision")' not in src and '["decision"]' not in src


@pytest.mark.parametrize("change", ["expired", "consumed", "time_expired", "no_ack", "none"])
def test_watcher_not_approved(change):
    s = rec(); a = appr(s); now = "2026-10-13T13:00:00-04:00"
    if change in ("expired", "consumed"):
        a["status"], a["status_changed_at"] = change, fx.at("12:40")
    elif change == "time_expired":
        now = "2026-10-13T16:00:00-04:00"
    elif change == "no_ack":
        a["acknowledged_flags"] = []
    else:
        s["approvals"].remove(a)
    t = wr.target_from_session(s, fx.TK, now=now)
    assert t["status"] == "approved_pending" and not t["approved"] and t["hypothetical"] and t["approval_id"] is None


def test_watcher_veto_wins_over_approval():
    s = rec(); appr(s)["review_id"] = "rv-postopen"
    assert wr.target_from_session(s, fx.TK)["status"] == "vetoed"
    s = rec(); rvw(s)["results"][0]["result"] = "VETO"          # tampered: stored FLAG, results VETO
    assert wr.target_from_session(s, fx.TK)["status"] == "vetoed"
    assert wr.target_from_session(fx.example(), fx.TK)["status"] == "vetoed"


@pytest.mark.parametrize("with_position", [True, False])
def test_watcher_revoked_is_rejected(with_position):
    """spec 1.1.4 ruling: a revoked approval makes the ticket a rejected shadow (fail closed), not a hypothetical"""
    s = fx.approved_record(with_position=with_position); a = appr(s)
    a["status"], a["status_changed_at"] = "revoked", fx.at("12:40")
    t = wr.target_from_session(s, fx.TK, now="2026-10-13T13:00:00-04:00")
    assert t == {**t, "status": "rejected", "approved": False, "hypothetical": True, "approval_id": None}
    assert t["status"] in wr.SHADOW


def test_watcher_real_approval_wins_over_older_revoked():
    s = rec(); old = appr(s); old["status"], old["status_changed_at"] = "revoked", fx.at("12:40")
    new = copy.deepcopy(old); new.update(approval_id="ap-2", status="active", approved_at=fx.at("12:45"))
    new.pop("status_changed_at", None); s["approvals"].append(new)
    t = wr.target_from_session(s, fx.TK, now="2026-10-13T13:00:00-04:00")
    assert t["status"] == "approved_pending" and t["approved"] and t["approval_id"] == "ap-2"


def test_watcher_revoked_beats_expired_or_consumed():
    """no real approval: any revoked approval of the ticket -> rejected, whatever the other approvals say"""
    for other in ("expired", "consumed"):
        s = rec(); a = appr(s); a["status"], a["status_changed_at"] = "revoked", fx.at("12:40")
        b = copy.deepcopy(a); b.update(approval_id="ap-2", status=other, status_changed_at=fx.at("12:50"))
        s["approvals"].append(b)
        assert wr.target_from_session(s, fx.TK, now="2026-10-13T13:00:00-04:00")["status"] == "rejected", other


def test_watcher_veto_still_wins_over_revoked():
    s = rec(); a = appr(s); a["status"], a["status_changed_at"] = "revoked", fx.at("12:40")
    rvw(s)["results"][0]["result"] = "VETO"
    assert wr.target_from_session(s, fx.TK, now="2026-10-13T13:00:00-04:00")["status"] == "vetoed"


# ------------------------------------------------------------------ back-compat: 1.0.0 .. 1.1.2 unchanged
BACKUP = SPEC / "_v1_1_2_backup"
FIXTURES = SPEC / "fixtures"   # vendored, trimmed historical records (spec 1.1.4); resolved from this file only
# historical records that must validate identically under every frozen validator; a missing file FAILS (no skip)
OLD_RECORD_FILES = [SPEC / "_v1_backup/session.example.json", SPEC / "_v1_1_0_backup/session.example.json",
                    SPEC / "_v1_1_1_backup/session.example.json", BACKUP / "session.example.json",
                    FIXTURES / "session-2026-10-13.json", FIXTURES / "session-2026-10-13.v1_1.json",
                    FIXTURES / "spec_patches_v1_1.session.example.json"]
_RUNNER = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import validate_session as vs
recs = json.load(sys.stdin)
print(json.dumps([vs.validate(r) for r in recs]))
"""


def frozen_validate(records):
    """errors from the frozen 1.1.2 validator, run in its own directory/process (its own risk_reference), no bytecode."""
    out = subprocess.run([PY, "-B", "-c", _RUNNER, str(BACKUP)], input=json.dumps(records), capture_output=True,
                         text=True, env=ENV, cwd=str(BACKUP), check=True)
    return json.loads(out.stdout)


def _old_records():
    missing = [str(p.relative_to(SPEC)) for p in OLD_RECORD_FILES if not p.is_file()]
    assert not missing, f"back-compat record(s) missing: {missing}"
    return [json.loads(p.read_text()) for p in OLD_RECORD_FILES]


def _mutated(version):
    """records that break every new 1.1.3 rule, labelled as an older version"""
    s = fx.approved_record(); s["schema_version"] = version
    variants = [copy.deepcopy(s)]
    m = copy.deepcopy(s); appr(m)["acknowledged_flags"] = []; variants.append(m)                       # INV-05 ack
    m = copy.deepcopy(s); a = appr(m); a["status"], a["status_changed_at"] = "revoked", fx.at("12:28"); variants.append(m)
    m = copy.deepcopy(s); a = appr(m); a["status"] = "consumed"; a.pop("status_changed_at", None); variants.append(m)
    m = copy.deepcopy(s); rvw(m)["results"][0]["result"] = "VETO"; variants.append(m)                  # INV-16
    m = copy.deepcopy(s); m["risk_reviews"][1]["overall"].update(verdict="PASS", blocking_rule_ids=[]); variants.append(m)
    m = copy.deepcopy(s); pos(m)["fills"][1]["fill_id"] = "f-1"; variants.append(m)                     # INV-03-FILL
    m = copy.deepcopy(s); pos(m)["fills"][0]["leg_id"] = "leg-9"; variants.append(m)                    # INV-04-FILL-LEG
    m = copy.deepcopy(s); appr(m)["review_id"] = "rv-postopen"; variants.append(m)                      # INV-05 VETO old text
    m = copy.deepcopy(s); pos(m)["fills"][0]["filled_at"] = fx.at("16:01"); variants.append(m)          # old INV-06 text
    return variants


def test_backcompat_old_records_identical_to_frozen_1_1_2():
    recs = _old_records()
    assert len(recs) == len(OLD_RECORD_FILES) == 7
    assert [vs.validate(r) for r in recs] == frozen_validate(recs)
    assert all(vs.validate(r) == [] for r in recs)


@pytest.mark.parametrize("version", ["1.1.0", "1.1.1", "1.1.2"])
def test_backcompat_mutations_identical_to_frozen_1_1_2(version):
    recs = _mutated(version)
    new = [vs.validate(r) for r in recs]
    assert new == frozen_validate(recs)
    for e in new:   # none of the 1.1.3-only ids leak into older records
        assert not any(x.split(" ", 1)[0] in {"INV-16-VERDICT", "INV-16-BLOCKING", "INV-16-FLAGS", "INV-16-EMPTY",
                                              "INV-05-ACK-MISSING", "INV-06-STATUS", "INV-03-FILL", "INV-04-FILL-LEG"} for x in e)


def test_backcompat_1_1_2_accepts_unacknowledged_flag_and_revoked_fill():
    """documents the old (1.1.2) behaviour that 1.1.3 closes: same record, only the version differs"""
    s = fx.approved_record(); appr(s)["acknowledged_flags"] = []
    a = appr(s); a["status"], a["status_changed_at"] = "revoked", fx.at("12:28")
    old = copy.deepcopy(s); old["schema_version"] = "1.1.2"
    assert vs.validate(old) == []
    assert {"INV-05-ACK-MISSING", "INV-06-STATUS"} <= set(ids(vs.validate(s)))


# ------------------------------------------------------------------ fail-closed CLI
def cli(*args, cwd=SPEC, script=SPEC / "validate_session.py"):
    return subprocess.run([PY, "-B", str(script), *map(str, args)], capture_output=True, text=True, env=ENV, cwd=str(cwd))


def test_cli_valid_exit_0(tmp_path):
    p = tmp_path / "ok.json"; p.write_text(json.dumps(rec()))
    r = cli(p); assert r.returncode == 0 and "OK:" in r.stdout
    assert cli().returncode == 0                          # default: session.example.json


def test_cli_invalid_exit_1(tmp_path):
    s = rec(); appr(s)["acknowledged_flags"] = []
    p = tmp_path / "bad.json"; p.write_text(json.dumps(s))
    r = cli(p); assert r.returncode == 1 and "INV-05-ACK-MISSING" in r.stdout and "OK" not in r.stdout


def test_cli_unknown_schema_version_exit_1(tmp_path):
    s = rec(); s["schema_version"] = "9.9.9"
    p = tmp_path / "v.json"; p.write_text(json.dumps(s))
    r = cli(p); assert r.returncode == 1 and "schema schema_version" in r.stdout


@pytest.mark.parametrize("kind", ["missing", "bad_json", "array", "string", "directory", "empty", "binary"])
def test_cli_unreadable_exit_2(tmp_path, kind):
    p = tmp_path / "x.json"
    if kind == "bad_json":
        p.write_text("{not json")
    elif kind == "array":
        p.write_text("[]")
    elif kind == "string":
        p.write_text('"session"')
    elif kind == "directory":
        p.mkdir()
    elif kind == "empty":
        p.write_text("")
    elif kind == "binary":
        p.write_bytes(b"\xff\xfe\x00\x81")
    r = cli(p)
    assert r.returncode == 2 and "ERROR" in r.stderr and "OK" not in r.stdout, (r.stdout, r.stderr)


def test_cli_mixed_files_worst_wins(tmp_path):
    ok = tmp_path / "ok.json"; ok.write_text(json.dumps(rec()))
    s = rec(); appr(s)["acknowledged_flags"] = []
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps(s))
    assert cli(ok, bad).returncode == 1
    assert cli(ok, tmp_path / "nope.json").returncode == 2
    assert cli(bad, tmp_path / "nope.json", ok).returncode == 2


def test_cli_validator_exception_exit_2(tmp_path, monkeypatch, capsys):
    p = tmp_path / "ok.json"; p.write_text(json.dumps(rec()))
    def boom(*a, **k):
        raise KeyError("internal")
    monkeypatch.setattr(vs, "validate", boom)
    assert vs.main([str(p)]) == 2
    out = capsys.readouterr()
    assert "validator raised KeyError" in out.err and "OK" not in out.out


def test_cli_schema_invalid_shape_does_not_crash(tmp_path):
    p = tmp_path / "e.json"; p.write_text("{}")
    r = cli(p); assert r.returncode == 1 and "schema" in r.stdout


def test_unimportable_validator_copy_fails_closed(tmp_path):
    """consumer contract: a broken/missing validator must never produce exit 0 or 'OK'"""
    d = tmp_path / "spec"; d.mkdir()
    for f in ("validate_session.py", "session.schema.json", "risk_reference.py", "risk_rules.yaml", "verticals.py",
              "session.example.json"):
        shutil.copy(SPEC / f, d / f)
    (d / "validate_session.py").write_text((d / "validate_session.py").read_text() + "\n)syntax error(\n")
    r = cli(d / "session.example.json", cwd=d, script=d / "validate_session.py")
    assert r.returncode != 0 and "OK" not in r.stdout
    (d / "session.schema.json").unlink(); (d / "validate_session.py").write_text((SPEC / "validate_session.py").read_text())
    r = cli(d / "session.example.json", cwd=d, script=d / "validate_session.py")
    assert r.returncode != 0 and "OK" not in r.stdout
    r = cli(d / "session.example.json", cwd=d, script=d / "missing_validator.py")
    assert r.returncode != 0


# ---------------------------------------------------------------- fix-plan rows 16, 17, 19 (text and labels)
HERE = Path(__file__).resolve().parent


def _yaml(name):
    import yaml
    return yaml.safe_load((HERE / name).read_text())


def test_watcher_md_case_count_matches_yaml():          # row 16
    import re
    n = len(_yaml("watcher_rules.yaml")["tests"])
    m = re.search(r"(\d+) test cases", (HERE / "watcher_rules.md").read_text())
    assert m and int(m.group(1)) == n == 59   # 1.1.4: + SCOPE-e/f/g (approval status -> target); 1.1.5: + SCOPE-h..r


def test_message_format_placeholders_are_alert_fields():  # row 17
    import re
    wr_yaml = _yaml("watcher_rules.yaml")
    ph = set(re.findall(r"{(\w+)}", wr_yaml["message_format"]))
    assert "detail" in wr_yaml["alert_fields"] and ph <= set(wr_yaml["alert_fields"])


def test_rule_file_labels_equal_version():                 # row 19
    v = (HERE / "VERSION").read_text().strip()
    assert v == "1.1.6"
    assert _yaml("risk_rules.yaml")["ruleset_version"] == v
    assert _yaml("watcher_rules.yaml")["watcher_version"] == v
    assert (HERE / "risk_rules.md").read_text().splitlines()[0].startswith(f"# Risk rules v{v} ")
    assert (HERE / "watcher_rules.md").read_text().splitlines()[0].startswith(f"# Watcher rules v{v} ")
    ex = json.loads((HERE / "session.example.json").read_text())
    assert {r["ruleset_version"] for r in ex["risk_reviews"]} == {v}


def test_watcher_rules_md_documents_status_only():
    t = (HERE / "watcher_rules.md").read_text()
    assert "read ONLY from `approvals[].status`" in t and "no `decision` field" in t
