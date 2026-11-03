"""SOL-ACCEPT-001 G8: run, validate, watch and spec-check check the spec folder against the pinned release at
runtime. An edited validate_session.py, a VERSION that is not the pin, or a missing validator gives a non-zero exit
and never an OK line (SPEC-PIN-MISMATCH on stderr, exit 2; a missing validator keeps SPEC-VALIDATOR-MISSING).

Behaviour-only: every case is a copy of the pinned spec/ in tmp_path, changed there (the repo's spec/ is never
touched), selected with OPTIONS_SPEC_DIR, and driven through the public CLI (`optpipe.cli.main`).
"""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from conftest import PINNED_SPEC
from optpipe.cli import main

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "2026-10-13"
TAPE = FIXTURES / "watch_tape.yaml"
ACCEPT_ALL = "def validate(rec, **kw):\n    return []\n"


def _spec(tmp_path, how):
    d = tmp_path / "spec"
    shutil.copytree(PINNED_SPEC, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    if how == "edited_validator":
        (d / "validate_session.py").write_text(ACCEPT_ALL)
    elif how == "version_9_9_9":
        (d / "VERSION").write_text("9.9.9\n")
    elif how == "missing_validator":
        (d / "validate_session.py").unlink()
    elif how == "edited_rules":                       # any manifest-listed file, not only the validator
        p = d / "risk_rules.md"
        p.write_text(p.read_text() + "\n<!-- edited -->\n")
    return d


HOWS = ["edited_validator", "version_9_9_9", "missing_validator", "edited_rules"]


def _use(monkeypatch, tmp_path, how):
    monkeypatch.setenv("OPTIONS_SPEC_DIR", str(_spec(tmp_path, how)))


def _bad_record(pipeline_output, tmp_path):
    """The run's own record with a schema-invalid approval (`decision`, status ACTIVE): Doodle's real validator
    rejects it, an accept-all validator would not."""
    r = json.loads(json.dumps(pipeline_output["record"]))
    rv = r["risk_reviews"][-1]
    r["approvals"] = [{"approval_id": "apr-x", "ticket_id": rv["ticket_id"], "review_id": rv["review_id"],
                       "decision": "approved", "status": "ACTIVE"}]
    d = tmp_path / "rec"
    d.mkdir()
    p = d / "session-2026-10-13.json"
    p.write_text(json.dumps(r, indent=2))
    return p


def _assert_refused(rc, cap, what, how):
    assert rc != 0, f"{what} exit 0 on a spec folder with {how}:\n{cap.out[-500:]}"
    assert "OK" not in cap.out, f"{what} printed an OK line on a spec folder with {how}:\n{cap.out[-500:]}"
    assert rc == 2, f"{what} exit {rc}, want 2"
    want = "SPEC-VALIDATOR-MISSING" if how == "missing_validator" else "SPEC-PIN-MISMATCH"
    assert want in cap.err, f"{what}: no {want} on stderr:\n{cap.err[-500:]}"


@pytest.mark.parametrize("how", HOWS)
def test_validate_unpinned_spec_exit_2(pipeline_output, tmp_path, monkeypatch, capsys, how):
    p = _bad_record(pipeline_output, tmp_path)
    _use(monkeypatch, tmp_path, how)
    capsys.readouterr()
    rc = main(["validate", str(p)])
    _assert_refused(rc, capsys.readouterr(), "optpipe validate", how)


@pytest.mark.parametrize("how", HOWS)
def test_run_unpinned_spec_exit_2_no_ok(tmp_path, monkeypatch, capsys, how):
    _use(monkeypatch, tmp_path, how)
    out = tmp_path / "out"
    capsys.readouterr()
    rc = main(["run", "--fixtures", str(FIXTURES), "--out", str(out)])
    cap = capsys.readouterr()
    assert "validate_session: OK" not in cap.out and "OK as " not in cap.out, f"run printed OK with {how}"
    _assert_refused(rc, cap, "optpipe run", how)
    assert not list(out.glob("session-*.json")), "run wrote a session record against an unpinned spec"


@pytest.mark.parametrize("how", HOWS)
def test_watch_unpinned_spec_exit_2(pipeline_output, tmp_path, monkeypatch, capsys, how):
    sess = pipeline_output["out"] / "session-2026-10-13.json"
    _use(monkeypatch, tmp_path, how)
    out = tmp_path / "wout"
    capsys.readouterr()
    rc = main(["watch", "--session", str(sess), "--replay", str(TAPE), "--once",
               "--clock", "2026-10-13T10:00:00-04:00", "--out", str(out)])
    cap = capsys.readouterr()
    assert "PAPER ONLY | watch" not in cap.out and not list(out.glob("*.watch.json")), f"watch ran with {how}"
    _assert_refused(rc, cap, "optpipe watch", how)


@pytest.mark.parametrize("how", HOWS)
def test_spec_check_unpinned_spec_nonzero(tmp_path, monkeypatch, capsys, how):
    _use(monkeypatch, tmp_path, how)
    capsys.readouterr()
    rc = main(["spec-check"])
    cap = capsys.readouterr()
    assert rc != 0, f"spec-check exit 0 on a spec folder with {how}"
    want = "SPEC-VALIDATOR-MISSING" if how == "missing_validator" else "SPEC-PIN-MISMATCH"
    assert want in cap.err, cap.err[-500:]


def test_pinned_copy_still_works(pipeline_output, tmp_path, monkeypatch, capsys):
    """Control: an untouched copy of the pinned spec validates the run's record and spec-check passes."""
    _use(monkeypatch, tmp_path, "none")
    capsys.readouterr()
    assert main(["spec-check"]) == 0, capsys.readouterr().err
    capsys.readouterr()
    rc = main(["validate", str(pipeline_output["out"] / "session-2026-10-13.json")])
    cap = capsys.readouterr()
    assert rc == 0 and "OK: 0 errors" in cap.out, cap.out + cap.err


def test_runtime_pin_matches_sync_spec_pin():
    """Consistency (not a baseline negative): the runtime pin is the pair scripts/sync_spec.py pins, so a re-pin
    that updates one without the other fails CI."""
    from optpipe import spec_pin
    sp = importlib.util.spec_from_file_location("sync_spec_for_pin", ROOT / "scripts" / "sync_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    assert (spec_pin.EXPECTED_VERSION, spec_pin.EXPECTED_MANIFEST_SHA256) == \
        (mod.EXPECTED_VERSION, mod.EXPECTED_MANIFEST_SHA256)


SPEC_1_1_6 = ("1.1.6", "6b4c51ce10c71a885d8e009436fedd28d9cf6cb1feaa5a704b63d7241099a647")


def test_runtime_pin_is_spec_1_1_6(tmp_path, monkeypatch, capsys):
    """Negative (#12 review): after the 1.1.6 re-pin (#16) the runtime pin must be 1.1.6 / 6b4c51ce..., so the
    pinned spec/ itself passes spec-check. A runtime still pinned to 1.1.5 / ecc73140 refuses the real spec
    (SPEC-PIN-MISMATCH, exit 2)."""
    from optpipe import spec_pin
    assert (spec_pin.EXPECTED_VERSION, spec_pin.EXPECTED_MANIFEST_SHA256) == SPEC_1_1_6
    assert spec_pin.pin_error(PINNED_SPEC) is None
    _use(monkeypatch, tmp_path, "none")
    capsys.readouterr()
    rc = main(["spec-check"])
    cap = capsys.readouterr()
    assert rc == 0 and "SPEC-PIN-MISMATCH" not in cap.err and "pinned release 1.1.6" in cap.out, cap.out + cap.err


def test_runtime_pin_is_read_from_sync_spec(tmp_path):
    """Single source of truth: the runtime pin is parsed from scripts/sync_spec.py (never executed), so a copy of
    sync_spec.py with other constants yields exactly those constants."""
    from optpipe import spec_pin
    src = (ROOT / "scripts" / "sync_spec.py").read_text()
    alt = src.replace(SPEC_1_1_6[1], "a" * 64).replace(f'EXPECTED_VERSION = "{SPEC_1_1_6[0]}"',
                                                        'EXPECTED_VERSION = "9.8.7"')
    assert alt != src
    (tmp_path / "sync_spec.py").write_text(alt)
    assert spec_pin.load_pin(tmp_path / "sync_spec.py") == ("9.8.7", "a" * 64, None)
    assert spec_pin.load_pin(ROOT / "scripts" / "sync_spec.py") == (*SPEC_1_1_6, None)


@pytest.mark.parametrize("how", ["missing", "not_literal", "bad_sha"])
def test_unreadable_pin_source_fails_closed(tmp_path, monkeypatch, capsys, how):
    """Fail closed: if scripts/sync_spec.py is missing or its pin is not a plain literal of the right shape, there
    is no runtime pin and even the untouched pinned spec is SPEC-PIN-MISMATCH (exit 2, no OK)."""
    from optpipe import spec_pin
    p = tmp_path / "sync_spec.py"
    if how == "not_literal":
        p.write_text('EXPECTED_VERSION = "1." + "1.6"\nEXPECTED_MANIFEST_SHA256 = "%s"\n' % SPEC_1_1_6[1])
    elif how == "bad_sha":
        p.write_text('EXPECTED_VERSION = "1.1.6"\nEXPECTED_MANIFEST_SHA256 = "xyz"\n')
    v, sha, why = spec_pin.load_pin(p)
    assert v is None and sha is None and why
    monkeypatch.setattr(spec_pin, "EXPECTED_VERSION", v)
    monkeypatch.setattr(spec_pin, "EXPECTED_MANIFEST_SHA256", sha)
    monkeypatch.setattr(spec_pin, "PIN_SOURCE_ERROR", why)
    assert spec_pin.pin_error(PINNED_SPEC).startswith("SPEC-PIN-MISMATCH")
    _use(monkeypatch, tmp_path, "none")
    capsys.readouterr()
    rc = main(["spec-check"])
    cap = capsys.readouterr()
    assert rc == 2 and "SPEC-PIN-MISMATCH" in cap.err and " OK" not in cap.out, cap.out + cap.err
