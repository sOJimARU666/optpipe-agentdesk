"""FIX-SEC-001 PR-A: `optpipe validate` exit codes follow Doodle's validate_session CLI (spec 1.1.3):
0 = valid, 1 = record errors, 2 = unreadable input, missing/broken validator or a validator crash. Every consumer
treats any non-zero, and a missing validator, as FAIL (validate_file / Orchestrator.validate never return [] for a
record that was not validated). Negative tests fail on 1d077a74. PAPER ONLY, synthetic records, no network."""
from __future__ import annotations

import json
import types

import pytest

import optpipe.orchestrator as orch
from optpipe.cli import main
from sec001_records import approved


def run(tmp_path, capsys, text: str) -> tuple[int, str]:
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(text)
    rc = main(["validate", str(p)])
    out = capsys.readouterr()
    return rc, out.out + out.err


def crashing(monkeypatch):
    def boom(rec, **kw):
        raise RuntimeError("validator bug")
    monkeypatch.setattr(orch, "load_validator", lambda spec: (types.SimpleNamespace(validate=boom), None))


def test_exit_0_valid(example, tmp_path, capsys):
    assert run(tmp_path, capsys, json.dumps(approved(example))) == (0, "OK: 0 errors\n")


def test_exit_1_record_errors(example, tmp_path, capsys):
    rc, out = run(tmp_path, capsys, json.dumps(approved(example, acknowledged=[])))
    assert rc == 1 and "INV-05-ACK-MISSING" in out


@pytest.mark.parametrize("text", ["{not json", ""], ids=["bad_json", "empty_file"])
def test_exit_2_unreadable_input(tmp_path, capsys, text):
    rc, out = run(tmp_path, capsys, text)
    assert rc == 2 and "SPEC-RECORD-UNREADABLE" in out


def test_exit_2_validator_crash(example, tmp_path, capsys, monkeypatch):
    crashing(monkeypatch)
    rc, out = run(tmp_path, capsys, json.dumps(approved(example)))
    assert rc == 2 and "SPEC-VALIDATOR-CRASHED" in out and "validator bug" in out


def test_validate_file_crash_is_fail_not_ok(example, settings, tmp_path, monkeypatch):
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(approved(example)))
    crashing(monkeypatch)
    errs = orch.validate_file(settings, p)
    assert errs and errs[0].startswith("SPEC-VALIDATOR-CRASHED")
    p.write_text("{not json")
    monkeypatch.undo()
    errs = orch.validate_file(settings, p)
    assert errs and errs[0].startswith("SPEC-RECORD-UNREADABLE")


def test_orchestrator_validate_crash_is_fail_not_ok(example, spec, monkeypatch):
    crashing(monkeypatch)
    o = orch.Orchestrator.__new__(orch.Orchestrator)
    o.spec = spec
    v = o.validate(approved(example))
    assert v["validator"] is None and v["errors"] and v["errors"][0].startswith("SPEC-VALIDATOR-CRASHED")
