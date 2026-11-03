"""FIX-SEC-001 R1 (Moodle's review of #5): optpipe's watcher must make the same ticket decision as Doodle's pinned
reference, spec/watcher_reference.target_from_session (VETO first: the latest review BY evaluated_at, or any
approval pointing at a VETO review, vetoes the ticket; an approval never un-vetoes it). Runs both over the same
records, including Moodle's 11-record comparison set (his review parity script, rebuilt here from
session.example.json).

Decision = (status, vetoed, really approved). It must be EXACTLY equal on every record here, valid or not: no N4
status exception, no "optpipe may be stricter" tolerance and (spec 1.1.5 ruling 1: an INV-16-inconsistent review is
VETO in the reference too) no tampered_pass exception. Precedence (spec 1.1.5 watcher_rules.md "Approval state",
TARGET_PRECEDENCE): 1 VETO first (incl. INV-16); 2 idea executed/closed keeps its status (a revoked approval does not
relabel it); 3 idea rejected, or any revoked approval and no real approval -> rejected; 4 idea approved_pending /
approved / none -> approved_pending (real or hypothetical), any other idea status kept, not watched; 5 a real approval
needs the idea approved_pending/approved plus an active, in-window, INV-05 and INV-16 clean approval (no idea + active
approval = hypothetical). Negative tests fail on 1d077a74 (R1) or 450c4c2 (PR #6). PAPER ONLY, synthetic records, no network."""
from __future__ import annotations

import copy
import inspect
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from optpipe.watcher.runtime import targets_from_session
from sec001_records import approved

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 13, 12, 30, tzinfo=NY)
NOWS = "2026-10-13T12:30:00-04:00"
TID = "tkt-jpm-bcs-340-350"


def at(hhmm: str) -> dict:
    h, m = map(int, hhmm.split(":"))
    return {"ny": f"2026-10-13T{h:02d}:{m:02d}:00-04:00", "berlin": f"2026-10-13T{h + 6:02d}:{m:02d}:00+02:00"}


def cases(ex: dict) -> dict[str, dict]:
    """Moodle's 11 records (names as in his parity.py), plus the R1 key cases."""
    c: dict[str, dict] = {"active_ok": approved(ex)}
    for s in ("revoked", "expired", "consumed"):
        c[s] = approved(ex, status=s)
    r = approved(ex); del r["approvals"][0]["status"]; r["approvals"][0]["decision"] = "approved"
    c["decision_only"] = r
    c["no_ack"] = approved(ex, acknowledged=None)
    r = approved(ex); r["approvals"][0]["review_id"] = "rv-premarket"
    c["approval_of_old_veto_review"] = r
    r = approved(ex); a2 = copy.deepcopy(r["approvals"][0]); a2.update(approval_id="apr-0", review_id="rv-premarket")
    r["approvals"].insert(0, a2)
    c["extra_approval_of_veto_review"] = r
    r = approved(ex); r["risk_reviews"] = r["risk_reviews"][::-1]
    c["reviews_out_of_order"] = r
    r = approved(ex); r["risk_reviews"][-1]["overall"]["verdict"] = "PASS"
    c["tampered_pass"] = r
    r = approved(ex); r["risk_reviews"][0]["evaluated_at"] = at("12:25")
    c["veto_latest_by_time_but_first_in_array"] = r                       # 11: Moodle's unsafe case (valid record)
    # R1 extras: a later VETO re-review listed BEFORE the earlier FLAG review (the key case, built explicitly)
    r = approved(ex)
    flag = r["risk_reviews"][-1]
    veto = copy.deepcopy(flag); veto.update(review_id="rv-rereview", evaluated_at=at("12:28"))
    for x in veto["results"]:
        if x["rule_id"] == "R01_QUOTE_FRESHNESS":
            x["result"] = "VETO"
    veto["overall"].update(verdict="VETO", blocking_rule_ids=["R01_QUOTE_FRESHNESS"])
    r["risk_reviews"].insert(0, veto)
    c["later_veto_rereview_listed_first"] = r
    # tie on evaluated_at: the one listed last is the latest (reference: stable sort) -> FLAG last = not vetoed
    r = approved(ex); r["risk_reviews"][0]["evaluated_at"] = copy.deepcopy(r["risk_reviews"][-1]["evaluated_at"])
    c["tie_flag_listed_last"] = r
    r = approved(ex); r["risk_reviews"][0]["evaluated_at"] = copy.deepcopy(r["risk_reviews"][-1]["evaluated_at"])
    r["risk_reviews"] = r["risk_reviews"][::-1]
    c["tie_veto_listed_last"] = r
    # PR #6 (spec 1.1.4): the full approval-status mapping, incl. several approvals for one ticket
    r = approved(ex); r["approvals"] = []
    c["no_approval"] = r
    c["active_out_of_window"] = approved(ex, expires_at=at("12:25"))
    for later in ("expired", "consumed", "active_out_of_window", "active_no_ack"):
        for order in ("then", "after"):
            r = approved(ex, status="revoked")
            a2 = copy.deepcopy(r["approvals"][0])
            a2.update(approval_id="apr-2", status=later.split("_")[0], approved_at=at("12:22"))
            if later == "active_out_of_window":
                a2["expires_at"] = at("12:25")
            if later == "active_no_ack":
                del a2["acknowledged_flags"]
            r["approvals"] = [r["approvals"][0], a2] if order == "then" else [a2, r["approvals"][0]]
            c[f"revoked_{order}_{later}"] = r
    r = approved(ex, status="revoked"); a2 = copy.deepcopy(r["approvals"][0])
    a2.update(approval_id="apr-2", status="active", approved_at=at("12:22"))
    r["approvals"].append(a2)
    c["revoked_then_real_approval"] = r                                    # a real approval wins
    # PR #6 C1 (Moodle): reference step 4, idea status outside (approved_pending, approved, no idea). The idea status
    # is set together with a matching history entry (INV-07), so all 20 records are valid (Moodle's istat_probe.py).
    for ist in NON_APPROVAL_IDEA_STATUSES:
        for base in ISTAT_BASES:
            r = copy.deepcopy(c[base])
            for i in r["idea_log"]:
                if i.get("ticket_id") == TID:
                    i["current_status"] = ist
                    h = copy.deepcopy(i["history"][-1]); h["status"] = ist; h["at"] = at("12:29"); h.pop("ref_ids", None)
                    i["history"].append(h)
            c[f"idea_{ist}_{base}"] = r
    # spec 1.1.5 ruling: no idea_log entry -> idea status None; an active approval is then HYPOTHETICAL, never real
    for base in ISTAT_BASES:
        r = copy.deepcopy(c[base])
        r["idea_log"] = [i for i in r["idea_log"] if i.get("ticket_id") != TID]
        c[f"no_idea_{base}"] = r
    return c


NON_APPROVAL_IDEA_STATUSES = ("proposed", "simulated", "executed", "closed")
ISTAT_BASES = ("active_ok", "revoked", "expired", "no_approval", "revoked_then_expired")
ISTAT_CASES = [f"idea_{i}_{b}" for i in NON_APPROVAL_IDEA_STATUSES for b in ISTAT_BASES]
NO_IDEA_CASES = [f"no_idea_{b}" for b in ISTAT_BASES]


@pytest.fixture(scope="module")
def ref(spec):
    sys.path.insert(0, str(spec.root))
    try:
        import watcher_reference
        import validate_session
        yield watcher_reference, validate_session
    finally:
        sys.path.remove(str(spec.root))


def ours(r: dict):
    kw = [NOW] if "now" in inspect.signature(targets_from_session).parameters else []   # 1d077a74 has no `now`
    t = next(t for t in targets_from_session(r, *kw) if t.kind == "ticket" and t.ticket_id == TID)
    return t.status, (t.status == "vetoed" or t.vetoed), (t.status == "approved_pending" and not t.hypothetical)


def theirs(ref, r: dict):
    t = ref[0].target_from_session(r, TID, NOWS)
    return t["status"], t["status"] == "vetoed", bool(t["approved"])


def errors(ref, r: dict) -> list[str]:
    """Record validity per Doodle's pinned validator (same verdict as validator + optpipe guards on every case here;
    spec-owned, so the 1d077a74 run judges validity identically)."""
    return list(ref[1].validate(copy.deepcopy(r)))




def divergent(ex, ref) -> list[str]:
    out = []
    for k, r in cases(ex).items():
        (s1, v1, a1), (s2, v2, a2) = ours(r), theirs(ref, r)
        if (s1, v1, a1) != (s2, v2, a2):
            out.append(f"{k}: optpipe {s1} vetoed={v1} approved={a1} vs reference {s2} vetoed={v2} approved={a2}")
    return out


def test_parity_with_reference_on_comparison_set(example, ref):
    assert divergent(example, ref) == []


@pytest.mark.parametrize("name", ["veto_latest_by_time_but_first_in_array", "later_veto_rereview_listed_first"])
def test_later_veto_rereview_listed_first_is_vetoed(example, ref, name):
    r = cases(example)[name]
    assert errors(ref, r) == []                                           # a VALID record
    assert theirs(ref, r)[:2] == ("vetoed", True)
    status, vetoed, approved_ = ours(r)
    assert status == "vetoed" and vetoed and not approved_


@pytest.mark.parametrize("name", ["approval_of_old_veto_review", "extra_approval_of_veto_review"])
def test_any_approval_of_a_veto_review_vetoes_the_ticket(example, ref, name):
    r = cases(example)[name]
    assert theirs(ref, r)[1] is True
    status, vetoed, approved_ = ours(r)
    assert status == "vetoed" and not approved_


def test_older_veto_listed_last_is_not_the_latest(example, ref):
    r = cases(example)["reviews_out_of_order"]
    assert errors(ref, r) == [] and theirs(ref, r)[:3] == ("approved_pending", False, True)
    assert ours(r) == ("approved_pending", False, True)


@pytest.mark.parametrize("name,vetoed", [("tie_flag_listed_last", False), ("tie_veto_listed_last", True)])
def test_evaluated_at_tie_break_matches_reference(example, ref, name, vetoed):
    r = cases(example)[name]
    assert theirs(ref, r)[1] is vetoed
    assert ours(r)[1] is vetoed


CASE_NAMES = [
    "active_ok", "revoked", "expired", "consumed", "decision_only", "no_ack", "approval_of_old_veto_review",
    "extra_approval_of_veto_review", "reviews_out_of_order", "veto_latest_by_time_but_first_in_array",
    "later_veto_rereview_listed_first", "tie_flag_listed_last", "tie_veto_listed_last",
    "no_approval", "active_out_of_window", "revoked_then_real_approval",
    *[f"revoked_{o}_{x}" for x in ("expired", "consumed", "active_out_of_window", "active_no_ack") for o in ("then", "after")],
    *ISTAT_CASES, *NO_IDEA_CASES, "tampered_pass"]


def test_case_names_cover_every_case(example):
    assert sorted(CASE_NAMES) == sorted(cases(example))


@pytest.mark.parametrize("name", CASE_NAMES)
def test_parity_per_record(example, ref, name):
    """Exact parity, valid or invalid record (spec 1.1.4 removed the N4 / "optpipe may be stricter" tolerances; 1.1.5 the tampered_pass exception)."""
    r = cases(example)[name]
    assert ours(r) == theirs(ref, r), (ours(r), theirs(ref, r))


def test_tampered_pass_is_vetoed_by_both(example, ref):
    """NEGATIVE vs the 1.1.4 reference (it approved this record): spec 1.1.5 ruling 1 makes an INV-16-inconsistent
    review VETO in watcher_reference too, so the old tampered_pass exception is gone and both sides veto it."""
    r = cases(example)["tampered_pass"]
    errs = errors(ref, r)
    assert len(errs) == 1 and errs[0].startswith("INV-16-VERDICT "), errs
    assert theirs(ref, r) == ours(r) == ("vetoed", True, False)


# The spec 1.1.4 approval-status table (watcher_rules.yaml scope.approval_status), as (status, vetoed, approved).
REAL, HYPO, REJECTED = ("approved_pending", False, True), ("approved_pending", False, False), ("rejected", False, False)
MAPPING = {"active_ok": REAL, "active_out_of_window": HYPO, "no_ack": HYPO, "revoked": REJECTED,
           "expired": HYPO, "consumed": HYPO, "no_approval": HYPO, "revoked_then_real_approval": REAL}


@pytest.mark.parametrize("name,want", sorted(MAPPING.items()))
def test_approval_status_mapping(example, ref, name, want):
    r = cases(example)[name]
    assert theirs(ref, r) == want                                         # the table is the reference's
    assert ours(r) == want


@pytest.mark.parametrize("name", [f"revoked_{o}_{x}" for x in ("expired", "consumed", "active_out_of_window",
                                                               "active_no_ack") for o in ("then", "after")])
def test_any_revoked_approval_rejects_unless_really_approved(example, ref, name):
    """NEGATIVE (fails on 450c4c2): a revoked approval followed (or preceded) by an expired / consumed / blocked active
    approval of the same ticket came out approved_pending HYPOTHETICAL (a watched what-if) in optpipe, because only
    the one picked approval's status was read. Spec 1.1.4: without a real approval, ANY revoked one -> rejected."""
    r = cases(example)[name]
    assert theirs(ref, r) == REJECTED
    t = next(t for t in targets_from_session(r, NOW) if t.kind == "ticket" and t.ticket_id == TID)
    assert (t.status, t.vetoed, t.hypothetical) == ("rejected", False, False), t.note


def _istat_want(name: str) -> tuple:
    """Spec 1.1.5 precedence for the idea-status probe records: executed/closed keep their status whatever the
    approvals (step 2); proposed/simulated with a revoked approval and no real one -> rejected (step 3); otherwise
    they keep their status (step 4b). Never really approved (step 5)."""
    ist, base = name.split("_", 2)[1], name.split("_", 2)[2]
    if ist not in ("executed", "closed") and base in ("revoked", "revoked_then_expired"):
        return ("rejected", False, False)
    return (ist, False, False)


@pytest.mark.parametrize("name", ISTAT_CASES)
def test_idea_not_approved_is_not_watched_whatever_the_approval(example, ref, name):
    """NEGATIVE (fails on 49debf0 / 450c4c2, PR #6 C1): an idea that is not approved_pending/approved is never really
    approved and never watched as a ticket. optpipe used to honour an active in-window approval of a `proposed` (or
    executed/closed) idea as REAL and watch it intraday, watch expired/no approval as a hypothetical, and turn a
    revoked approval of an executed/closed idea into rejected. Valid records only (Moodle's istat_probe.py)."""
    r = cases(example)[name]
    assert errors(ref, r) == []
    from optpipe.records.guards import check_record
    assert check_record(r) == []
    want = _istat_want(name)
    assert theirs(ref, r) == want
    t = next(t for t in targets_from_session(r, NOW) if t.kind == "ticket" and t.ticket_id == TID)
    assert (t.status, t.vetoed, t.status == "approved_pending" and not t.hypothetical) == want, t.note
    assert not t.watched and t.header()["watched_intraday"] is False
    assert t.shadow is (want[0] == "rejected")                          # rejected = shadow EOD; kept status = nothing


@pytest.mark.parametrize("name,want", [
    ("idea_proposed_revoked", ("rejected", False, False)),
    ("idea_simulated_revoked", ("rejected", False, False)),
    ("idea_executed_revoked", ("executed", False, False)),
    ("idea_closed_revoked_then_expired", ("closed", False, False)),
    ("idea_proposed_active_ok", ("proposed", False, False)),
    ("no_idea_active_ok", ("approved_pending", False, False)),
])
def test_spec_1_1_5_precedence_key_cases(example, ref, name, want):
    """The key rows of the 1.1.5 precedence (handoff / watcher_rules.md), both sides."""
    r = cases(example)[name]
    assert errors(ref, r) == []
    assert theirs(ref, r) == want and ours(r) == want


def test_unwatched_idea_gets_no_quotes_and_no_alerts(example, spec, raw_rules):
    """C1 end to end: the watcher fetches nothing and alerts nothing for a `proposed` idea with a real-looking
    approval (it would have been polled intraday as really approved before)."""
    from optpipe.risk.engine import Calendar
    from optpipe.risk.ruleset import MarketHours
    from optpipe.watcher.runtime import Watcher
    r = cases(example)["idea_proposed_active_ok"]
    r["paper_positions"] = []
    class NoQuotes:
        name, method, calls = "none", "GET", []
        def snapshot(self, *a, **k):
            raise AssertionError("no quote may be fetched for an unwatched ticket")
    cal = Calendar(MarketHours.model_validate(raw_rules["config"]["market_hours"]))
    w = Watcher(r, spec.watcher_rules(), cal, NoQuotes(), now=NOW)
    t = next(t for t in w.targets if t.ticket_id == TID and t.kind == "ticket")
    assert t.status == "proposed" and not t.watched
    for k in range(3):
        w.step(NOW.replace(minute=30 + k))
    assert w.alerts == [] and all(x["quote_calls"] == 0 for x in w.wakes)


def test_missing_idea_defaults_to_no_status(example, ref):
    """C1 (2) + spec 1.1.5 ruling: no idea_log entry is idea status None (reference), not 'proposed'; the ticket is
    watched as approved_pending, but an active approval is HYPOTHETICAL, never really approved (fail closed)."""
    r = cases(example)["no_idea_active_ok"]
    t = next(t for t in targets_from_session(r, NOW) if t.kind == "ticket" and t.ticket_id == TID)
    assert t.record_status is None and t.status == "approved_pending" and t.hypothetical and t.watched, t.note
    assert ours(r) == theirs(ref, r) == HYPO


def test_reference_mapping_is_the_yaml_table(spec, ref):
    """The pinned spec's YAML table and reference agree, and revoked is rejected (no N4 divergence left to document)."""
    m = spec.watcher_rules()["scope"]["approval_status"]
    assert m == ref[0].APPROVAL_STATUS_TARGET and m["revoked"] == "rejected"
    import optpipe.watcher.runtime as rt
    assert "N4" not in (rt.targets_from_session.__doc__ or "") and "divergence" not in (rt.targets_from_session.__doc__ or "")


def test_now_is_required():
    with pytest.raises(TypeError):
        targets_from_session({"tickets": []})                            # no wall-clock default (N7)


def test_watcher_now_is_required_keyword():
    from optpipe.watcher.runtime import Watcher
    p = inspect.signature(Watcher).parameters.get("now")
    assert p is not None and p.default is inspect.Parameter.empty and p.kind is inspect.Parameter.KEYWORD_ONLY


def test_dashboard_latest_review_uses_the_watcher_rule(example, ref):
    """N6: the dashboard's 'latest' review is the watcher's (by evaluated_at), not run order / list position."""
    from types import SimpleNamespace
    from optpipe.dashboard.app import tickets_model
    r = cases(example)["veto_latest_by_time_but_first_in_array"]
    runs = [{"run_id": x["run_id"]} for x in r.get("runs") or []] if isinstance(r.get("runs"), list) else []
    row = next(t for t in tickets_model(SimpleNamespace(record=r, runs=runs)) if t["t"]["ticket_id"] == TID)
    assert row["latest"]["review_id"] == "rv-premarket" and row["latest"]["overall"]["verdict"] == "VETO"


def test_precedence_constants_are_the_reference(spec, ref):
    """optpipe's step-2 / step-5 idea sets are the pinned reference's, and the reference's TARGET_PRECEDENCE is the
    pinned spec's own `scope.target_precedence` (spec/watcher_rules.yaml), not a list copied into this test."""
    import optpipe.watcher.runtime as rt
    wr = ref[0]
    yaml_precedence = spec.watcher_rules()["scope"]["target_precedence"]
    assert isinstance(yaml_precedence, list) and yaml_precedence          # the key exists and is non-empty
    assert tuple(wr.TARGET_PRECEDENCE) == tuple(yaml_precedence)
    assert rt.TERMINAL_IDEA_STATUSES == wr.TERMINAL_IDEA_STATUSES
    assert rt.APPROVED_IDEA_STATUSES == wr.APPROVED_IDEA_STATUSES
