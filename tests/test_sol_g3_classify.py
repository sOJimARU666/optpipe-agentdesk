"""SOL-ACCEPT-001 G3: rule-result classification fails closed. An unknown result string ("veto", "BLOCK", None, "")
or an UNKNOWN result with no severity is BLOCKING, so a review whose stored overall says PASS over such a result is
VETO for every consumer: the watcher, the idea log and the run summary.

Behaviour-only: records are the tree's own offline run output (`pipeline_output`), tampered here; the consumers are
called through their public entry points (watcher `targets_from_session` via a signature adapter, idea-log
`apply_reviews`, `cli.summary`). On a tree without the fix the assertion that fails is the verdict / status.
"""
import copy
import inspect
from datetime import datetime, timezone

import pytest

from optpipe.cli import summary
from optpipe.records.idea_log import apply_reviews
from optpipe.records.store import read_trace
from optpipe.watcher.runtime import targets_from_session

DATE = "2026-10-13"
TID = "tkt-jpm-bcs-340-350"
NOW = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)       # 10:00 New York, after the run
BAD_RESULTS = ["veto", "BLOCK", None, ""]


def _targets(rec):
    """Adapter: the watcher library takes `now` on newer trees, only the record on older ones."""
    if "now" in inspect.signature(targets_from_session).parameters:
        return targets_from_session(rec, NOW)
    return targets_from_session(rec)


def _ticket_target(rec):
    return next(t for t in _targets(rec) if t.kind == "ticket" and t.ticket_id == TID)


def _record(pipeline_output, first: dict, overall_verdict="PASS", flags=()):
    """The run's review of TID with every rule result PASS except results[0] := `first`; the stored overall claims
    `overall_verdict`. The idea is approved_pending, so the review is the only thing that can veto the ticket."""
    rec = copy.deepcopy(pipeline_output["record"])
    rv = next(r for r in rec["risk_reviews"] if r["ticket_id"] == TID)
    for x in rv["results"]:
        x["result"], x["severity"] = "PASS", x.get("severity", "hard")
        x.pop("leg_results", None)
    rv["results"][0].update(first)
    for k in [k for k, v in first.items() if v is _DROP]:
        rv["results"][0].pop(k)
    rv["overall"].update(verdict=overall_verdict, blocking_rule_ids=[], flag_rule_ids=list(flags))
    idea = next(i for i in rec["idea_log"] if i.get("ticket_id") == TID)
    idea["current_status"] = "approved_pending"
    return rec, rv


_DROP = object()


@pytest.mark.parametrize("bad", BAD_RESULTS, ids=repr)
def test_watcher_unknown_result_string_is_veto(pipeline_output, bad):
    rec, _ = _record(pipeline_output, {"result": bad})
    t = _ticket_target(rec)
    assert t.status == "vetoed" and t.vetoed, f"result {bad!r} read as pass: watched as {t.status}"


def test_watcher_unknown_without_severity_is_veto(pipeline_output):
    rec, _ = _record(pipeline_output, {"result": "UNKNOWN", "severity": _DROP})
    t = _ticket_target(rec)
    assert t.status == "vetoed" and t.vetoed, f"UNKNOWN without severity read as pass: watched as {t.status}"


def test_watcher_unknown_with_other_severity_is_veto(pipeline_output):
    rec, _ = _record(pipeline_output, {"result": "UNKNOWN", "severity": "medium"})
    t = _ticket_target(rec)
    assert t.status == "vetoed", f"UNKNOWN with severity 'medium' read as pass: watched as {t.status}"


def test_watcher_soft_unknown_flag_is_not_vetoed(pipeline_output):
    """Control: a consistent soft-UNKNOWN FLAG review is not vetoed by classification (same on every tree)."""
    rec, rv = _record(pipeline_output, {"result": "UNKNOWN", "severity": "soft"}, "FLAG")
    rv["overall"]["flag_rule_ids"] = [rv["results"][0]["rule_id"]]
    assert _ticket_target(rec).status == "approved_pending"


@pytest.mark.parametrize("bad", BAD_RESULTS, ids=repr)
def test_idea_log_unknown_result_string_is_vetoed(pipeline_output, bad):
    rec, rv = _record(pipeline_output, {"result": bad})
    ideas = copy.deepcopy(rec["idea_log"])
    for i in ideas:
        i["current_status"] = "proposed"
    ideas, _ = apply_reviews(ideas, [rv], NOW)
    idea = next(i for i in ideas if i.get("ticket_id") == TID)
    assert idea["current_status"] == "vetoed", f"result {bad!r}: idea moved to {idea['current_status']}"


def test_idea_log_unknown_without_severity_is_vetoed(pipeline_output):
    rec, rv = _record(pipeline_output, {"result": "UNKNOWN", "severity": _DROP})
    ideas, _ = apply_reviews(copy.deepcopy(rec["idea_log"]), [rv], NOW)
    assert next(i for i in ideas if i.get("ticket_id") == TID)["current_status"] == "vetoed"


@pytest.mark.parametrize("bad", BAD_RESULTS, ids=repr)
def test_summary_unknown_result_string_is_veto(pipeline_output, bad):
    rec, _ = _record(pipeline_output, {"result": bad})
    trace = read_trace(pipeline_output["out"] / f"session-{DATE}.trace.json")
    line = next(ln for ln in summary(rec, trace).splitlines() if TID in ln)
    assert line.split()[0] == "VETO", line


def test_classify_equals_pinned_spec_1_1_6_risk_reference(spec):
    """G3 aligned with spec 1.1.6: optpipe's classify is exactly risk_reference.classify (None == 'pass') over every
    recognised and unrecognised result / severity, and on non-object entries."""
    import itertools
    import sys
    from optpipe.records.guards import classify
    sys.path.insert(0, str(spec.root))
    try:
        import risk_reference as rr
    finally:
        sys.path.remove(str(spec.root))
    results = ["PASS", "FLAG", "UNKNOWN", "VETO", "veto", "Pass", "BLOCK", "", None, 0, 1, ["PASS"], "MISSING"]
    sevs = ["hard", "soft", "HARD", "x", "", None, 1, "MISSING"]
    rows = []
    for res, sev in itertools.product(results, sevs):
        x = {"rule_id": "R"}
        if res != "MISSING":
            x["result"] = res
        if sev != "MISSING":
            x["severity"] = sev
        rows.append(x)
    rows += [None, "PASS", 1, ["PASS"]]
    for x in rows:
        assert (classify(x) or "pass") == rr.classify(x), x
