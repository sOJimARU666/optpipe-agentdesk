"""SOL-ACCEPT-001 G5: the record guards turn naive / unparsable times and wrong field types into INV errors and
never crash; a guard crash is exit 2 (like a validator crash), never a traceback and never OK.

Behaviour-only: every check goes through the public `optpipe validate` CLI (`optpipe.cli.main`) on records built
from Doodle's spec/session.example.json with the shared tests/sec001_records.py builders (an approved FLAG review
plus an open paper position, which the offline run does not produce).
"""
import copy
import json

import pytest

from optpipe.cli import main
from sec001_records import approved, with_position


def _validate(tmp_path, rec, capsys):
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(rec, indent=2))
    capsys.readouterr()
    rc = main(["validate", str(p)])          # an uncaught guard exception fails the test here
    o = capsys.readouterr()
    return rc, o.out, o.err


def _naive(pair):
    return {"ny": pair["ny"][:19], "berlin": pair["berlin"][:19]}


@pytest.fixture
def positioned(example):
    return with_position(approved(example))


def test_positioned_example_is_valid(tmp_path, positioned, capsys):
    """Control: the untouched builder record validates on every tree."""
    rc, out, err = _validate(tmp_path, positioned, capsys)
    assert rc == 0 and "OK" in out, out + err


def test_naive_filled_at_is_error_not_crash(tmp_path, positioned, capsys):
    rec = copy.deepcopy(positioned)
    for f in rec["paper_positions"][0]["fills"]:
        f["filled_at"] = _naive(f["filled_at"])
    rc, out, err = _validate(tmp_path, rec, capsys)
    assert rc == 1, out + err
    assert "INV-06-TIME-NAIVE f-1" in out, f"naive filled_at not reported as an INV-06 error:\n{out}{err}"
    assert "OK" not in out


def test_naive_approved_at_is_error_not_crash(tmp_path, positioned, capsys):
    rec = copy.deepcopy(positioned)
    rec["approvals"][0]["approved_at"] = _naive(rec["approvals"][0]["approved_at"])
    rc, out, err = _validate(tmp_path, rec, capsys)
    assert rc == 1, out + err
    assert "INV-05-TIME-NAIVE apr-1 approved_at" in out, f"naive approved_at not reported as an INV-05 error:\n{out}{err}"


def test_naive_expires_at_is_error_not_crash(tmp_path, positioned, capsys):
    rec = copy.deepcopy(positioned)
    rec["approvals"][0]["expires_at"] = _naive(rec["approvals"][0]["expires_at"])
    rc, out, err = _validate(tmp_path, rec, capsys)
    assert rc == 1, out + err
    assert "INV-05-TIME-NAIVE apr-1 expires_at" in out, f"naive expires_at not reported as an INV-05 error:\n{out}{err}"


def _set(path):
    def f(rec, value):
        o = rec
        for k in path[:-1]:
            o = o[k]
        o[path[-1]] = value
    return f


WRONG_TYPES = {
    "approved_at=str": (_set(["approvals", 0, "approved_at"]), "2026-10-13T12:20:00-04:00", "INV-05-TIME-INVALID apr-1 approved_at"),
    "expires_at=int": (_set(["approvals", 0, "expires_at"]), 1760371200, "INV-05-TIME-INVALID apr-1 expires_at"),
    "fills=dict": (_set(["paper_positions", 0, "fills"]), {"f-1": {"action": "buy_to_open"}}, "INV-06-FILLS pos-1"),
    "results=str": (_set(["risk_reviews", -1, "results"]), "PASS", "INV-16-TYPE"),
    "overall=list": (_set(["risk_reviews", -1, "overall"]), ["PASS"], "INV-16-TYPE"),
}


@pytest.mark.parametrize("case", list(WRONG_TYPES))
def test_check_record_wrong_types_never_raise(tmp_path, positioned, capsys, case):
    setter, value, want = WRONG_TYPES[case]
    rec = copy.deepcopy(positioned)
    setter(rec, value)
    rc, out, err = _validate(tmp_path, rec, capsys)
    assert rc == 1, f"{case}: exit {rc}\n{out}{err}"
    assert want in out, f"{case}: no guard error {want!r}:\n{out}{err}"


def test_validate_cli_guard_crash_exit_2(tmp_path, positioned, capsys, monkeypatch):
    """A guard that raises (simulated bug in the per-record walk) is exit 2 with GUARD-CRASHED, never OK."""
    def boom(rec):
        raise RuntimeError("simulated guard bug")
    try:
        import optpipe.records.guards as g
        monkeypatch.setattr(g, "_check_record", boom, raising=False)
    except ImportError:          # a tree without record guards: nothing to patch; the exit-code assertion decides
        pass
    rc, out, err = _validate(tmp_path, positioned, capsys)
    assert rc == 2, f"guard crash gave exit {rc}:\n{out}{err}"
    assert "GUARD-CRASHED" in err and "OK" not in out
