"""FIX-SEC-001 R1 (Moodle's review of #5): optpipe's watcher must make the same ticket decision as Doodle's pinned
reference, spec/watcher_reference.target_from_session (VETO first: the latest review BY evaluated_at, or any
approval pointing at a VETO review, vetoes the ticket; an approval never un-vetoes it). Runs both over the same
records, including Moodle's 11-record comparison set (his review parity script, rebuilt here from
session.example.json).

Decision = (vetoed, really approved). For a record that validates (Doodle's validator: 0 errors)
the decision must be EQUAL. For an invalid record optpipe may only be stricter (vetoed whenever the reference is,
approved only when the reference is). Status strings match too, except the documented N4 divergence: a revoked
approval is `rejected` (shadow) in optpipe and `approved_pending` hypothetical in the reference.
Negative tests fail on 1d077a74. PAPER ONLY, synthetic records, no network."""
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
N4 = {"revoked"}                                    # documented status-string divergence (decision still equal)


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
    return c


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
        if errors(ref, r):
            if (v2 and not v1) or (a1 and not a2):
                out.append(f"{k}: optpipe {s1} vetoed={v1} approved={a1} vs reference {s2} vetoed={v2} approved={a2}")
        elif (v1, a1) != (v2, a2) or (s1 != s2 and k not in N4):
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


@pytest.mark.parametrize("name", [
    "active_ok", "revoked", "expired", "consumed", "decision_only", "no_ack", "approval_of_old_veto_review",
    "extra_approval_of_veto_review", "reviews_out_of_order", "tampered_pass", "veto_latest_by_time_but_first_in_array",
    "later_veto_rereview_listed_first", "tie_flag_listed_last", "tie_veto_listed_last"])
def test_parity_per_record(example, ref, name):
    r = cases(example)[name]
    (s1, v1, a1), (s2, v2, a2) = ours(r), theirs(ref, r)
    if errors(ref, r):                                                    # invalid: optpipe may only be stricter
        assert (v1 or not v2) and (a2 or not a1), (s1, v1, a1, s2, v2, a2)
    else:
        assert (v1, a1) == (v2, a2), (s1, v1, a1, s2, v2, a2)
        assert s1 == s2 or name in N4, (s1, s2)


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
