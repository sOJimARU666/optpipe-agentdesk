"""FIX-SEC-001 P0-5: `optpipe run` and every validation entry point fail closed when Doodle's validate_session.py
is missing or cannot be imported. On 1d077a7, `run` printed "validate_session: OK (spec validator not found)"
and exited 0. The pinned spec/ is only copied (to tmp), never modified. PAPER ONLY, offline fixtures."""
from __future__ import annotations

import copy
import json
import shutil
import sys
from types import SimpleNamespace

import pytest

from optpipe.cli import main, summary
from optpipe.orchestrator import Orchestrator, validate_file
from optpipe.spec import discover
from conftest import FIXTURES, PINNED_SPEC

VALIDATOR_MISSING, VALIDATOR_BROKEN = "SPEC-VALIDATOR-MISSING", "SPEC-VALIDATOR-IMPORT-FAILED"
PIN_MISMATCH = "SPEC-PIN-MISMATCH"
CODES = {"missing": VALIDATOR_MISSING, "broken": PIN_MISMATCH, "broken_pin_check_off": VALIDATOR_BROKEN}


@pytest.fixture(params=list(CODES))
def bad_spec(request, tmp_path, monkeypatch):
    """A tmp copy of the pinned spec whose validator is missing or raises on import.

    SOL-ACCEPT-001 G8: a broken validator is an edited one, so the runtime pin check now refuses it first
    (SPEC-PIN-MISMATCH). "broken_pin_check_off" switches the pin check off to keep covering the import-failure
    path (SPEC-VALIDATOR-IMPORT-FAILED) behind it."""
    d = tmp_path / "spec"
    shutil.copytree(PINNED_SPEC, d, ignore=shutil.ignore_patterns("__pycache__"))
    if request.param == "missing":
        (d / "validate_session.py").unlink()
    else:
        (d / "validate_session.py").write_text('raise ImportError("simulated broken validator")\n')
    monkeypatch.setattr(sys, "path", list(sys.path))               # undo the spec loader's sys.path append
    monkeypatch.setenv("OPTIONS_SPEC_DIR", str(d))
    if request.param == "broken_pin_check_off":
        import optpipe.spec_pin
        monkeypatch.setattr(optpipe.spec_pin, "pin_error", lambda root: None)
    return SimpleNamespace(dir=d, kind=request.param, code=CODES[request.param])


def test_run_fails_when_validator_missing(bad_spec, tmp_path, capsys):
    out = tmp_path / "out"
    try:
        rc = main(["run", "--fixtures", str(FIXTURES), "--out", str(out)])
    except Exception as e:  # noqa: BLE001 - the old code crashed on a broken import; that is not a clean refusal
        pytest.fail(f"run raised {type(e).__name__}: {e} instead of refusing with rc 2")
    cap = capsys.readouterr()
    assert rc == 2
    assert "OK" not in cap.out
    assert bad_spec.code in cap.err and "refusing to run" in cap.err
    assert not (out / "session-2026-10-13.json").exists()          # nothing written


def test_summary_never_ok_without_validator(pipeline_output):
    from optpipe.records import read_trace
    out = pipeline_output["out"]
    rec = copy.deepcopy(pipeline_output["record"])
    trace = read_trace(out / "session-2026-10-13.trace.json")
    trace.runs[-1].validation = {"validator": None, "errors": [], "note": "validator unavailable"}
    line = next(x for x in summary(rec, trace).splitlines() if x.startswith("validate_session:"))
    assert "OK" not in line and "NOT validated" in line


def test_orchestrator_validate_reports_the_missing_validator(bad_spec, example):
    v = Orchestrator.validate(SimpleNamespace(spec=discover(bad_spec.dir)), example)
    assert v["validator"] is None and v["errors"] and v["errors"][0].startswith(bad_spec.code)


def test_validate_file_returns_an_error_not_none(bad_spec, settings, example, tmp_path):
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(example))
    errs = validate_file(settings, p)
    assert isinstance(errs, list) and errs and errs[0].startswith(bad_spec.code)


def test_validate_still_exit_2_when_missing(bad_spec, example, tmp_path, capsys):
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(example))
    try:
        rc = main(["validate", str(p)])
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"validate raised {type(e).__name__}: {e} instead of exiting 2")
    assert rc == 2 and bad_spec.code in capsys.readouterr().err


def test_run_still_ok_with_the_pinned_validator(pipeline_output):
    assert pipeline_output["rc"] == 0
