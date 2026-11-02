"""FIX-SEC-001 PR #6: spec 1.1.5 re-pin (was 1.1.4 in the first PR #6 commit). Records are written as 1.1.5 (same checks
as 1.1.3 / 1.1.4, no new invariants), the ruleset / watcher labels come from the pinned spec, 1.1.3 and 1.1.4 records
(and the frozen examples) still validate and load, and undated reviews sort deterministically. Negative tests fail on
450c4c2 (undated reviews) / 49debf0 (1.1.5 labels). PAPER ONLY, synthetic records, no network."""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from optpipe.records import compat
from optpipe.records.guards import check_record, latest_review, reviews_by_time


def test_records_are_written_as_1_1_5_with_spec_labels(pipeline_output, spec):
    rec = pipeline_output["record"]
    ruleset = str(yaml.safe_load(spec.path("risk_rules").read_text())["ruleset_version"])
    assert rec["schema_version"] == compat.V11 == "1.1.5"
    assert ruleset == "1.1.5" and rec["session"].get("risk_ruleset_version", ruleset) == ruleset
    assert rec["risk_reviews"] and {r["ruleset_version"] for r in rec["risk_reviews"]} == {ruleset}
    assert str(spec.watcher_rules()["watcher_version"]) == "1.1.5"


def _validator_errors(spec, rec):
    from optpipe.orchestrator import load_validator
    mod, err = load_validator(spec)
    assert mod is not None, err
    return list(mod.validate(copy.deepcopy(rec))) + check_record(rec)


@pytest.mark.parametrize("old", ["1.1.3", "1.1.4"])
def test_frozen_example_still_validates(spec, old):
    ex = json.loads((spec.root / f"_v1_1_{old[-1]}_backup" / "session.example.json").read_text())
    assert ex["schema_version"] == old
    assert _validator_errors(spec, ex) == []


@pytest.mark.parametrize("old", ["1.1.3", "1.1.4"])
def test_pipeline_record_relabelled_old_still_validates_and_loads(pipeline_output, spec, old):
    """A 1.1.3 record (PR #5) or 1.1.4 record (49debf0) validates with the same result under spec 1.1.5, and optpipe
    still reads it: store/dashboard accept it (V11_SHAPE) and from_v1 relabels it to 1.1.5 without other changes."""
    rec = copy.deepcopy(pipeline_output["record"])
    rec["schema_version"] = old
    assert _validator_errors(spec, rec) == []
    assert {"1.1.3", "1.1.4", "1.1.5"} <= set(compat.V11_SHAPE)
    up = compat.from_v1(rec)
    assert up["schema_version"] == "1.1.5" and {**up, "schema_version": old} == rec


def test_1_1_5_record_gets_the_1_1_3_checks(pipeline_output, spec):
    """No new invariants in 1.1.4 or 1.1.5: a broken INV-05 ack is reported identically for all three labels."""
    base = copy.deepcopy(pipeline_output["record"])
    out = {}
    for v in ("1.1.3", "1.1.4", "1.1.5"):
        r = copy.deepcopy(base)
        r["schema_version"] = v
        r["approvals"] = [{"approval_id": "apr-x", "ticket_id": "nope", "review_id": "nope", "status": "active"}]
        out[v] = sorted(_validator_errors(spec, r))
    assert out["1.1.3"] and out["1.1.3"] == out["1.1.4"] == out["1.1.5"]


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
