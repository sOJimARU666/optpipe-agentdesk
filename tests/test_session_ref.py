"""trace.session_ref.sha256 = canonical hash of the session record WITHOUT its top-level trace_ref.

The session points at the trace (trace_ref) and the trace points back (session_ref). Hashing the record minus
trace_ref breaks the cycle: the trace's hash can change without invalidating session_ref, and any edit to the
record's content does invalidate it.
"""
from __future__ import annotations

import json

import pytest

from optpipe.cli import main
from optpipe.records.store import SESSION_REF_COVERS, session_content_sha256, verify_session_ref


def _paths(out):
    out = out["out"]
    return out / "session-2026-10-13.json", out / "session-2026-10-13.trace.json"


def test_session_ref_is_filled_and_verifies(pipeline_output):
    j, t = _paths(pipeline_output)
    ref = json.loads(t.read_text())["session_ref"]
    rec = json.loads(j.read_text())
    assert ref["sha256"] == session_content_sha256(rec) and len(ref["sha256"]) == 64
    assert ref["sha256_covers"] == SESSION_REF_COVERS and "minus top-level trace_ref" in SESSION_REF_COVERS
    assert ref["schema_version"] == rec["schema_version"] == "1.1.8"
    ok, msg = verify_session_ref(j, json.loads(t.read_text()), rec)
    assert ok is True, msg


def test_tampered_record_fails(pipeline_output, tmp_path):
    j, t = _paths(pipeline_output)
    rec = json.loads(j.read_text())
    rec["risk_reviews"][0]["review_id"] += "-x"           # any content edit
    bad = tmp_path / j.name
    bad.write_text(json.dumps(rec, indent=2))
    ok, msg = verify_session_ref(bad, json.loads(t.read_text()), rec)
    assert ok is False and msg


def test_session_ref_verification_does_not_read_the_record_path(pipeline_output, tmp_path):
    """The hash is the record the caller already loaded. A missing path, a different file, or a
    symlink to other bytes must not be opened."""
    j, t = _paths(pipeline_output)
    rec = json.loads(j.read_text())
    trace = json.loads(t.read_text())
    missing = tmp_path / "not-present" / j.name
    try:
        ok, msg = verify_session_ref(missing, trace, rec)
    except OSError as exc:
        pytest.fail(f"verify_session_ref opened the caller path: {exc}")
    assert ok is True, msg

    planted = tmp_path / j.name
    planted.write_text('{"tampered":true}')
    ok, msg = verify_session_ref(planted, trace, rec)
    assert ok is True, msg
    assert "tampered" not in msg

    outside = tmp_path / "outside-record.json"
    outside.write_text('{"marker":"DO-NOT-READ"}')
    alias = tmp_path / "links" / j.name
    alias.parent.mkdir()
    alias.symlink_to(outside)
    ok, msg = verify_session_ref(alias, trace, rec)
    assert ok is True, msg
    assert "DO-NOT-READ" not in msg


def test_tampered_record_fails_even_when_the_path_is_untouched(pipeline_output):
    j, t = _paths(pipeline_output)
    rec = json.loads(j.read_text())
    rec["risk_reviews"][0]["review_id"] += "-x"
    ok, msg = verify_session_ref(j, json.loads(t.read_text()), rec)
    assert ok is False and msg


def test_trace_ref_and_formatting_do_not_affect_it(pipeline_output):
    j, _ = _paths(pipeline_output)
    rec = json.loads(j.read_text())
    h = session_content_sha256(rec)
    other = {**rec, "trace_ref": {**rec["trace_ref"], "sha256": "0" * 64}}
    assert session_content_sha256(other) == h                                 # no cycle through the trace hash
    assert session_content_sha256(json.loads(json.dumps(rec, indent=4))) == h  # key order / whitespace irrelevant


def test_cli_validate_checks_session_ref(pipeline_output, tmp_path, capsys):
    j, t = _paths(pipeline_output)
    assert main(["validate", str(j)]) == 0
    assert "session_ref OK" in capsys.readouterr().out
    out = tmp_path / "x"
    out.mkdir()
    rec = json.loads(j.read_text())
    rec["notes"] = list(rec.get("notes") or []) + ["edited after the run"]
    (out / j.name).write_text(json.dumps(rec, indent=2))
    (out / t.name).write_text(t.read_text())
    assert main(["validate", str(out / j.name)]) != 0
    assert "session_ref" in capsys.readouterr().out
