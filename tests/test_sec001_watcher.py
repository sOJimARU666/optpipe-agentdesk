"""FIX-SEC-001 P0-4: the watcher reads an approval's `status` (Doodle's approval schema), never a `decision`
field (not in the schema), honours only an approval that stands at the watch start, and `optpipe watch` refuses
a session record that does not validate. PAPER ONLY: no network (replay tape), synthetic records."""
from __future__ import annotations

import ast
import copy
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from sec001_records import FLAGS, approved
from optpipe.watcher.runtime import targets_from_session

NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
TAPE = ROOT / "tests" / "fixtures" / "2026-10-13" / "watch_tape.yaml"
NOW = datetime(2026, 10, 13, 12, 30, tzinfo=NY)          # inside apr-1's window (12:20 to 16:00 NY)
TID = "tkt-jpm-bcs-340-350"


def target(rec, now=NOW):
    return next(t for t in targets_from_session(rec, now) if t.kind == "ticket" and t.ticket_id == TID)


def test_watcher_active_approval_is_approved(example):
    t = target(approved(example))
    assert t.status == "approved_pending" and not t.hypothetical and not t.vetoed, t.note
    assert "apr-1" in t.note


@pytest.mark.parametrize("decision", ["approved", "approve", "rejected"])
def test_watcher_ignores_decision_field(example, decision):
    """A non-schema `decision` key changes nothing: with no `status`, nothing is approved or rejected."""
    r = approved(example)
    a = r["approvals"][0]
    del a["status"]
    a["decision"] = decision
    t = target(r)
    assert t.status == "approved_pending" and t.hypothetical, t.note
    assert "not active" in t.note
    # and an active approval stays approved whatever `decision` says
    a["status"] = "active"
    t = target(r)
    assert not t.hypothetical and t.status == "approved_pending", t.note


def test_watcher_revoked_approval_not_approved(example):
    t = target(approved(example, status="revoked"))
    assert t.status == "rejected" and t.shadow and not t.hypothetical, t.note


@pytest.mark.parametrize("status,now", [
    ("expired", NOW),                                          # status says expired
    ("consumed", NOW),                                         # already used
    ("active", datetime(2026, 10, 13, 16, 0, tzinfo=NY)),      # active, but expires_at has passed
    ("active", datetime(2026, 10, 13, 12, 0, tzinfo=NY)),      # active, but before approved_at
    ("active", datetime(2026, 10, 14, 12, 30, tzinfo=NY)),     # next day
])
def test_watcher_expired_approval_not_approved(example, status, now):
    t = target(approved(example, status=status), now)
    assert t.hypothetical and t.status == "approved_pending", t.note
    assert "approval NOT honoured" in t.note


@pytest.mark.parametrize("acks", [[], FLAGS[:1], None])
def test_watcher_unacknowledged_flags_not_approved(example, acks):
    t = target(approved(example, acknowledged=acks))
    assert t.hypothetical and "OPT-ACK" in t.note, t.note


def test_watcher_review_whose_results_veto_not_approved(example):
    r = approved(example)
    rv = next(x for x in r["risk_reviews"] if x["review_id"] == r["approvals"][0]["review_id"])
    next(x for x in rv["results"] if x["severity"] == "hard")["result"] = "VETO"   # overall still says FLAG
    t = target(r)
    assert t.hypothetical and "OPT-VERDICT" in t.note, t.note


def test_watcher_picks_the_approval_that_stands(example):
    """An old revoked approval followed by a fresh active one: the active one wins."""
    r = approved(example)
    old = copy.deepcopy(r["approvals"][0]) | {"approval_id": "apr-0", "status": "revoked"}
    r["approvals"].insert(0, old)
    t = target(r)
    assert not t.hypothetical and t.status == "approved_pending" and "apr-1" in t.note, t.note


def _bad_session(pipeline_output, tmp_path, how):
    d = tmp_path / "in"
    shutil.copytree(pipeline_output["out"], d)
    p = d / "session-2026-10-13.json"
    rec = json.loads(p.read_text())
    tid = rec["tickets"][0]["ticket_id"]
    if how == "decision_approval":          # the old watcher honoured this; Doodle's schema rejects it
        rec["approvals"] = [{"ticket_id": tid, "decision": "approved"}]
    elif how == "schema_violation":
        rec["session"]["mode"] = "live"
    elif how == "tampered_verdict":         # schema-valid, but overall says PASS while its results VETO
        for rv in rec["risk_reviews"]:
            rv["overall"].update(verdict="PASS", blocking_rule_ids=[], flag_rule_ids=[], approval_required=False)
    p.write_text(json.dumps(rec, indent=2))
    return p


@pytest.mark.parametrize("how", ["decision_approval", "schema_violation", "tampered_verdict"])
def test_watch_refuses_schema_invalid_session(pipeline_output, tmp_path, capsys, how):
    from optpipe.cli import main
    sess = _bad_session(pipeline_output, tmp_path, how)
    out_dir = tmp_path / "out"
    rc = main(["watch", "--session", str(sess), "--replay", str(TAPE), "--out", str(out_dir)])
    cap = capsys.readouterr()
    assert rc == 2, cap.out
    assert "refusing to watch" in cap.err and "does not validate" in cap.err
    assert not list(out_dir.glob("*.watch.json")) and "PAPER ONLY | watch" not in cap.out


def test_watch_refuses_when_validator_missing(pipeline_output, tmp_path, monkeypatch, capsys):
    from optpipe.cli import main
    from conftest import PINNED_SPEC
    spec = tmp_path / "spec"
    shutil.copytree(PINNED_SPEC, spec)
    (spec / "validate_session.py").unlink()
    monkeypatch.setenv("OPTIONS_SPEC_DIR", str(spec))
    sess = pipeline_output["out"] / "session-2026-10-13.json"
    rc = main(["watch", "--session", str(sess), "--replay", str(TAPE), "--out", str(tmp_path / "out")])
    assert rc == 2 and "SPEC-VALIDATOR-MISSING" in capsys.readouterr().err


# Reading an approval's non-schema `decision` key is the bug. The only legitimate `decision` read is the
# idea_log alternatives' own `decision` field (spec session.schema.json alternatives[].decision) in markdown.py.
DECISION_OK = {"src/optpipe/records/markdown.py"}


def _decision_reads(text: str) -> list[int]:
    lines = []
    for n in ast.walk(ast.parse(text)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args \
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == "decision":
            lines.append(n.lineno)
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value == "decision" \
                and isinstance(n.ctx, ast.Load):
            lines.append(n.lineno)
    return lines


def test_no_decision_reads_in_src():
    hits = []
    for p in sorted((ROOT / "src" / "optpipe").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel not in DECISION_OK:
            hits += [f"{rel}:{ln}" for ln in _decision_reads(p.read_text())]
    assert hits == []
    assert _decision_reads('a.get("decision")\nx = b["decision"]\n') == [1, 2]   # the check itself works
    assert re.search(r"alt\['decision'\]", (ROOT / "src/optpipe/records/markdown.py").read_text())
