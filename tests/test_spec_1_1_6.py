"""SOL-ACCEPT-001 G12: spec 1.1.6 re-pin (was 1.1.5, FIX-SEC-001 PR #6; this file was test_spec_1_1_5.py). Records are
written as 1.1.8 (same shape and checks as 1.1.6 / 1.1.7: the 1.1.3 checks plus G3/G4/G5/G6), the ruleset / watcher labels come from the
pinned spec, 1.1.3, 1.1.4 and 1.1.5 records (and the frozen examples) still validate and load under their own rules,
and undated reviews sort deterministically. Negative tests fail on 450c4c2 (undated reviews) / 49debf0 (labels) /
021aed2 (1.1.6 pin). PAPER ONLY, synthetic records, no network."""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from optpipe.records import compat
from optpipe.records.guards import check_record, latest_review, reviews_by_time


def test_records_are_written_as_1_1_6_with_spec_labels(pipeline_output, spec):
    rec = pipeline_output["record"]
    ruleset = str(yaml.safe_load(spec.path("risk_rules").read_text())["ruleset_version"])
    assert rec["schema_version"] == compat.V11 == "1.1.8"
    assert ruleset == "1.1.8" and rec["session"].get("risk_ruleset_version", ruleset) == ruleset
    assert rec["risk_reviews"] and {r["ruleset_version"] for r in rec["risk_reviews"]} == {ruleset}
    assert str(spec.watcher_rules()["watcher_version"]) == "1.1.8"   # records and labels are both 1.1.8 (PR B)


def _validator_errors(spec, rec):
    from optpipe.orchestrator import load_validator
    mod, err = load_validator(spec)
    assert mod is not None, err
    return list(mod.validate(copy.deepcopy(rec))) + check_record(rec)


OLD = ["1.1.3", "1.1.4", "1.1.5"]


@pytest.mark.parametrize("old", OLD)
def test_frozen_example_still_validates(spec, old):
    ex = json.loads((spec.root / f"_v1_1_{old[-1]}_backup" / "session.example.json").read_text())
    assert ex["schema_version"] == old
    assert _validator_errors(spec, ex) == []


@pytest.mark.parametrize("old", OLD)
def test_pipeline_record_relabelled_old_still_validates_and_loads(pipeline_output, spec, old):
    """A 1.1.3 (PR #5), 1.1.4 (49debf0) or 1.1.5 (f7a295e) record validates with the same result under spec 1.1.6, and
    optpipe still reads it: store/dashboard accept it (V11_SHAPE) and from_v1 relabels it to 1.1.8 without other changes."""
    rec = copy.deepcopy(pipeline_output["record"])
    rec["schema_version"] = old
    assert _validator_errors(spec, rec) == []
    assert {"1.1.3", "1.1.4", "1.1.5", "1.1.6", "1.1.7", "1.1.8"} <= set(compat.V11_SHAPE)
    up = compat.from_v1(rec)
    assert up["schema_version"] == "1.1.8" and {**up, "schema_version": old} == rec


def test_1_1_6_record_gets_the_1_1_3_checks(pipeline_output, spec):
    """No new invariants in 1.1.4 or 1.1.5, and 1.1.6 keeps the 1.1.3 INV-05 checks: a broken INV-05 ack is reported
    identically for all four labels."""
    base = copy.deepcopy(pipeline_output["record"])
    out = {}
    for v in ("1.1.3", "1.1.4", "1.1.5", "1.1.6"):
        r = copy.deepcopy(base)
        r["schema_version"] = v
        r["approvals"] = [{"approval_id": "apr-x", "ticket_id": "nope", "review_id": "nope", "status": "active"}]
        out[v] = sorted(_validator_errors(spec, r))
    assert out["1.1.3"] and out["1.1.3"] == out["1.1.4"] == out["1.1.5"] == out["1.1.6"]


def test_undated_reviews_sort_deterministically():
    """NEGATIVE (fails on 450c4c2, optional item): undated (schema-invalid) reviews sort first and, among themselves,
    by review_id, so the order does not depend on the array order. Dated ties keep array order (reference rule)."""
    u1, u2 = {"review_id": "rv-a"}, {"review_id": "rv-b", "evaluated_at": None}
    d = {"review_id": "rv-0", "evaluated_at": {"ny": "2026-10-13T12:00:00-04:00"}}
    for order in ([u2, d, u1], [u1, u2, d], [d, u2, u1]):
        assert [r["review_id"] for r in reviews_by_time(order)] == ["rv-a", "rv-b", "rv-0"]
    t1 = {"review_id": "rv-z", "evaluated_at": {"ny": "2026-10-13T12:00:00-04:00"}}
    t2 = {"review_id": "rv-y", "evaluated_at": {"ny": "2026-10-13T12:00:00-04:00"}}
    assert latest_review([t1, t2]) is t2 and latest_review([t2, t1]) is t1


def test_watcher_rules_scope_precedence_is_the_only_source(spec):
    """Moodle (SOL-ACCEPT-001) asked that the redundant top-level `target_precedence` of spec 1.1.6 never drift from
    `scope.target_precedence`; spec 1.1.7 removed the duplicate (pinned here since FEAT-OODLE-01 PR A, spec 1.1.8), so
    `scope.target_precedence` is the only source and keeps the same order."""
    wr = spec.watcher_rules()
    assert "target_precedence" not in wr
    assert list(wr["scope"]["target_precedence"]) == ["vetoed", "terminal", "rejected", "approved_pending", "idea_status"]


@pytest.mark.parametrize("old", OLD)
@pytest.mark.parametrize("where,inv", [("expires_at", "INV-06-EXPIRED"), ("approved_at", "INV-06-BEFORE")])
def test_old_records_keep_the_closed_fill_window_new_ones_do_not(example, spec, old, where, inv):
    """G12: 1.1.3-1.1.5 records still validate under their own (closed) INV-06 window in the pinned validator; the
    same record labelled 1.1.6 gets the open-window error. Validator only (optpipe's guards are G4)."""
    from optpipe.orchestrator import load_validator
    from sec001_records import approved, with_position
    mod, err = load_validator(spec)
    assert mod is not None, err
    out = {}
    for v in (old, "1.1.6"):
        r = with_position(approved(example))
        r["schema_version"] = v
        at = r["approvals"][0][where]
        for f in r["paper_positions"][0]["fills"]:
            f["filled_at"] = copy.deepcopy(at)
        out[v] = {e.split(" ", 1)[0] for e in mod.validate(copy.deepcopy(r))}
    assert inv not in out[old] and out[old] <= {"INV-06-STATUS"}, out[old]
    assert inv in out["1.1.6"]
