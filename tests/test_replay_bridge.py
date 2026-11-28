"""Replay Bridge 001 (src/optpipe/research/replay_bridge.py). PAPER ONLY. Every input here is SYNTHETIC / TEST FIXTURE:
the vendored frozen Lesson 001 v3 decisions (tests/fixtures/lesson001_v3) or a mutation / built row from it.

Covers the 7 fixes (labels, inner tampers, evidence, guard/existing-folder/missing-price tests, atomic output and
receipt negative controls, price status, freshness policy) and Doodle's PROPOSED price-freshness cases PF-01..PF-38.
"""
from __future__ import annotations

import ast
import copy
import csv
import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from optpipe.research import replay_bridge as rb

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lesson001_v3"
RAW = (FIX / "decisions.json").read_bytes()
RECEIPT = json.loads((FIX / "receipt.json").read_bytes())
SOL_CSV_SHA = "039ee5883a49fbd82e74a921bf18d6c2a551b52f7adfdda5a01d35ff0b58de24"   # Sol's evidence tape
S = 30                                         # the SIGNAL_BULL row (2026-11-20T10:00:00-05:00)
POLICY = rb.load_policy(json.loads((ROOT / "config" / "replay_bridge_price_policy.json").read_text()))
TF = "TEST-FIXTURE-pf"                         # source_id for built TEST FIXTURE prices
sha = rb.digest


def run_project(tape=None, receipt=None, *, raw=None, redigest=True, policy=POLICY, price_kind=None):
    if raw is None:
        raw = RAW if tape is None else json.dumps(tape).encode()
    r = dict(RECEIPT if receipt is None else receipt)
    if redigest and tape is not None:
        r["decisions_sha256"], r["count"] = sha(raw), len(tape)
    return rb.project(raw, r, policy, fixture_sha="TEST-FIXTURE-" + sha(raw)[:12], price_kind=price_kind)


def tape():
    return json.loads(RAW)


def rows(data: bytes):
    return list(csv.DictReader(io.StringIO(data.decode())))


def make_bundle(tmp_path, raw=RAW, receipt=None) -> tuple[Path, str]:
    p = tmp_path / "TEST-FIXTURE-lesson001-v3.zip"
    with zipfile.ZipFile(p, "w") as z:
        for name, data in ((rb.BASE + "decisions.json", raw),
                           (rb.BASE + "receipt.json", json.dumps(receipt or RECEIPT).encode())):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 21, 16, 21, 0))
            z.writestr(info, data)
    return p, sha(p.read_bytes())


def cli(args, archive_sha, cwd=None):
    """Run the real module CLI in a fresh Python process (netguard sitecustomize is on PYTHONPATH via conftest)."""
    code = ("import sys\nfrom optpipe.research import replay_bridge as rb\n"
            f"sys.exit(rb.main(sys.argv[1:], _archive_sha={archive_sha!r}))")
    return subprocess.run([sys.executable, "-c", code, *map(str, args)], capture_output=True, text=True, cwd=cwd,
                          check=False)


# ------------------------------------------------------------------------------------------- fixture + frozen output
def test_vendored_fixture_is_the_frozen_tape():
    assert sha(RAW) == rb.FROZEN_DECISIONS_SHA == RECEIPT["decisions_sha256"]
    receipt_sha = "5e0d3ef5178da18529b4c42586c6e3542aca65d4adedfbf85b466137aa0d1c91"
    assert sha((FIX / "receipt.json").read_bytes()) == receipt_sha


def test_pf35_frozen_tape_byte_identical_and_one_block():
    files, s = run_project()
    assert sha(files["decision_tape.csv"]) == SOL_CSV_SHA                 # Sol's 10-column tape unchanged
    assert s["rows"] == 391
    assert s["counts"] == s["upstream_counts"] == {"ABSTAIN": 375, "BLOCKED": 15, "SIGNAL_BEAR": 0, "SIGNAL_BULL": 1}
    log = rows(files["price_block_log.csv"])
    assert [(r["row_index"], r["decision_at_ny"], r["reason_code"], r["status"], r["age_seconds"]) for r in log] == \
        [("0", "2026-11-20T09:30:00-05:00", "PF-STATUS", "missing", "")]
    assert s["price_blocks"] == {"total": 1, "by_reason": {"PF-STATUS": 1}}
    lab = rows(files["decision_tape_labeled.csv"])
    assert sum(r["price_status"] == "OK" and r["price_age_seconds"] == "0" for r in lab) == 390
    assert files == run_project()[0]                                           # deterministic


@pytest.mark.skipif(not os.environ.get("LESSON001_BUNDLE"), reason="set LESSON001_BUNDLE to the frozen v3 zip")
def test_pf35_real_frozen_archive():
    files, s = rb.convert(Path(os.environ["LESSON001_BUNDLE"]), POLICY)
    assert s["archive_sha256"] == rb.ARCHIVE_SHA and sha(files["decision_tape.csv"]) == SOL_CSV_SHA
    assert s["price_blocks"]["total"] == 1


# ------------------------------------------------------------------------------------------- fix 1 + 6: labels, status
def test_every_csv_row_is_labeled_paper_only_decision_support_with_fixture():
    files, _ = run_project()
    fx = "TEST-FIXTURE-" + sha(RAW)[:12]
    for name in ("decision_tape_labeled.csv", "price_block_log.csv"):
        rs = rows(files[name])
        assert rs
        for r in rs:
            assert (r["paper_only"], r["decision_support"], r["fixture_set"], r["fixture_set_sha256"]) == \
                (rb.PAPER_ONLY, rb.DECISION_SUPPORT, rb.FIXTURE_SET, fx)


def test_price_status_column_is_explicit():
    lab = rows(run_project()[0]["decision_tape_labeled.csv"])
    assert {r["price_status"] for r in lab} == {"OK", "BLOCKED"}
    assert all((r["price_status"] == "OK") == (r["price_reason_code"] == "") for r in lab)
    assert all((r["price_status"] == "OK") == (r["observed_price"] != "") for r in lab)


# ------------------------------------------------------------------------------------------- fix 2: inner tampers
def _swap(t):
    t[10], t[11] = t[11], t[10]


def _blocked_ok_price(t):
    i = next(i for i, d in enumerate(t) if d["action"] == "BLOCKED" and d["indicators"]["price"]["status"] == "ok")
    t[i]["action"] = "SIGNAL_BULL"


def _blocked_no_price(t):
    i = next(i for i, d in enumerate(t) if d["action"] == "BLOCKED" and d["indicators"]["price"]["status"] != "ok")
    t[i]["action"] = "SIGNAL_BULL"


def _set(path, value):
    def f(t):
        obj = t[S]
        for k in path[:-1]:
            obj = obj[k]
        obj[path[-1]] = value
    return f


ABORT = "abort"
# Zoodle's 33 inner controls (controls/inner_controls.py), each re-created here. "abort" = refused, nothing exported.
# "block:CODE" = exported with that price blocked and the signal downgraded (Doodle's policy: a price problem is
# logged, never promoted, and no longer aborts). Either way no tampered signal survives.
TAMPERS = {
    "I01_reordered_stale_receipt": ({"mut": _swap, "redigest": False}, ABORT),
    "I02_reordered_redigested": ({"mut": _swap}, ABORT),
    "I03_truncated_stale": ({"raw": RAW[:len(RAW) // 2], "redigest": False}, ABORT),
    "I04_truncated_redigested": ({"raw": RAW[:len(RAW) // 2], "receipt_digest": True}, ABORT),
    "I05_dropped_row_gap": ({"mut": lambda t: t.pop(100)}, ABORT),                                   # was uncaught
    "I06_dropped_row_stale_count": ({"mut": lambda t: t.pop(100), "count": 391}, ABORT),
    "I07_duplicate_row": ({"mut": lambda t: t.__setitem__(31, copy.deepcopy(t[30]))}, ABORT),
    "I08_one_byte_stale": ({"raw": RAW.replace(b'"102"', b'"103"', 1), "redigest": False}, ABORT),
    "I09_signal_price_missing_no_source": ({"mut": _set(["indicators", "price"], {"status": "missing"})}, ABORT),
    "I10_signal_price_no_source": ({"mut": lambda t: t[S]["indicators"]["price"].pop("source_id")}, ABORT),
    "I11_price_source_mismatch": ({"mut": _set(["indicators", "price", "source_id"], "other")}, ABORT),
    "I12_iv_rank_invented": ({"mut": _set(["indicators", "iv_rank"], {"value": "42", "status": "ok"})}, ABORT),
    "I13_bull_vwap_missing": ({"mut": _set(["indicators", "vwap"], {"status": "missing"})}, ABORT),
    "I14_bull_range_missing": ({"mut": lambda t: [t[S]["indicators"].__setitem__(k, {"status": "missing"})
                                                   for k in ("range_high", "range_low")]}, ABORT),
    "I15_bull_failed_condition": ({"mut": _set(["conditions", "bull_vwap"], "Fail")}, ABORT),
    "I16_abstain_to_bear": ({"mut": lambda t: t[100].__setitem__("action", "SIGNAL_BEAR")}, ABORT),
    "I17_blocked_no_price_to_bull": ({"mut": _blocked_no_price}, ABORT),
    "I18_blocked_price_ok_to_bull": ({"mut": _blocked_ok_price}, ABORT),
    "I19_exit_fill": ({"mut": _set(["exit"], {"action": "FILL"})}, ABORT),
    "I20_exit_status_known": ({"mut": _set(["exit", "status"], "Known")}, ABORT),
    "I21_mode_live": ({"mut": _set(["mode"], "live")}, ABORT),
    "I22_provenance_real": ({"mut": _set(["provenance", "kind"], "REAL")}, ABORT),
    "I23_receipt_kind_real": ({"rec": {"kind": "REAL"}}, ABORT),
    "I24_receipt_fixture_false": ({"rec": {"fixture_assumptions": False}}, ABORT),
    "I25_unknown_action": ({"mut": _set(["action"], "SIGNAL_MAYBE")}, ABORT),
    "I26_future_price": ({"mut": _set(["indicators", "price", "as_of"], "2026-11-20T10:01:00-05:00")},
                         "block:PF-FUTURE"),
    "I27_price_nan": ({"mut": _set(["indicators", "price", "value"], "NaN")}, "block:PF-VALUE"),
    "I28_price_negative": ({"mut": _set(["indicators", "price", "value"], "-5")}, "block:PF-VALUE"),
    "I29_price_garbage": ({"mut": _set(["indicators", "price", "value"], "abc")}, "block:PF-VALUE"),
    "I30_naive_clock": ({"mut": _set(["clock", "ny"], "2026-11-20T10:00:00")}, ABORT),
    "I31_ny_berlin_mismatch": ({"mut": _set(["clock", "berlin"], "2026-11-20T17:00:00+01:00")}, ABORT),
    "I32_schema_changed": ({"mut": _set(["schema_version"], "lesson001.2")}, ABORT),
    "I33_missing_key": ({"mut": lambda t: t[S].pop("provenance")}, ABORT),
}
UNCAUGHT_BEFORE = {"I05_dropped_row_gap", "I12_iv_rank_invented", "I13_bull_vwap_missing", "I14_bull_range_missing",
                   "I15_bull_failed_condition", "I16_abstain_to_bear", "I18_blocked_price_ok_to_bull",
                   "I20_exit_status_known"}


def _apply(spec):
    t = tape()
    if "mut" in spec:
        spec["mut"](t)
    raw = spec.get("raw", json.dumps(t).encode() if "mut" in spec else RAW)
    r = dict(RECEIPT)
    if spec.get("redigest", True) and ("mut" in spec or spec.get("receipt_digest")):
        r["decisions_sha256"] = sha(raw)
        if "mut" in spec:
            r["count"] = len(t)
    if "count" in spec:
        r["count"] = spec["count"]
    r.update(spec.get("rec", {}))
    return raw, r


def test_tamper_table_has_all_33_and_the_8_former_gaps():
    assert len(TAMPERS) == 33 and UNCAUGHT_BEFORE <= set(TAMPERS)
    assert all(TAMPERS[k][1] == ABORT for k in UNCAUGHT_BEFORE)


@pytest.mark.parametrize("case", sorted(TAMPERS))
def test_inner_tamper_is_caught(case):
    spec, expected = TAMPERS[case]
    raw, r = _apply(spec)
    if expected == ABORT:
        with pytest.raises(rb.TapeError):
            rb.project(raw, r, POLICY, fixture_sha="TEST-FIXTURE")
        return
    files, s = rb.project(raw, r, POLICY, fixture_sha="TEST-FIXTURE", price_kind="bar_close_1m")
    assert s["counts"]["SIGNAL_BULL"] == 0 and s["counts"]["BLOCKED"] == 16          # downgraded, not promoted
    log = rows(files["price_block_log.csv"])
    assert [r["reason_code"] for r in log if r["row_index"] == str(S)] == [expected.split(":")[1]]


def test_i00_repacked_unmodified_is_exported():
    raw, r = _apply({"mut": lambda t: None})
    files, _s = rb.project(raw, r, POLICY, fixture_sha="TEST-FIXTURE", price_kind="bar_close_1m")
    assert sha(files["decision_tape.csv"]) == SOL_CSV_SHA


def test_tamper_disposition_documented_in_repo():
    doc = (ROOT / "docs" / "replay_bridge_001.md").read_text()
    for case in UNCAUGHT_BEFORE:
        assert case.split("_")[0] in doc


# ------------------------------------------------------------------------------------------- fix 3 + 5: output, receipt
NOW = datetime(2026, 11, 28, 13, 0, tzinfo=UTC)


def _export(tmp_path, name="out", **kw):
    bundle, asha = make_bundle(tmp_path)
    out = tmp_path / name
    r = rb.export(bundle, out, kw.pop("policy_path", ROOT / "config" / "replay_bridge_price_policy.json"),
                  command="python -m optpipe.research.replay_bridge export b out", now=NOW, archive_sha=asha)
    return out, r


def test_receipt_records_code_hash_command_timestamp_and_sha256sums(tmp_path):
    out, r = _export(tmp_path)
    assert sorted(p.name for p in out.iterdir()) == sorted(rb.OUTPUT_FILES + ("SHA256SUMS",))
    assert r["code_sha256"] == sha(Path(rb.__file__).read_bytes())
    assert r["command"] == "python -m optpipe.research.replay_bridge export b out"
    assert r["generated_at_utc"] == "2026-11-28T13:00:00+00:00"
    assert r["price_policy"]["status"] == "PROPOSED"
    assert r["price_policy"]["max_age_seconds"] == {"bar_close_1m": "0", "quote_last": "900"}
    sums = dict(reversed(line.split("  ")) for line in (out / "SHA256SUMS").read_text().splitlines())
    assert sums == {n: sha((out / n).read_bytes()) for n in rb.OUTPUT_FILES}
    assert json.loads((out / "bridge_receipt.json").read_bytes()) == r
    assert rb.verify(out)["rows"] == 391


def test_atomic_failure_leaves_no_output_and_no_partial(tmp_path, monkeypatch):
    real = rb._write

    def flaky(path, data):
        if path.name == "bridge_receipt.json":
            raise OSError(28, "No space left on device (TEST FIXTURE)")
        real(path, data)
    monkeypatch.setattr(rb, "_write", flaky)
    with pytest.raises(OSError):
        _export(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["TEST-FIXTURE-lesson001-v3.zip"]   # no out, no .partial


def test_atomic_interrupt_leaves_nothing(tmp_path, monkeypatch):
    real = rb._write

    def interrupt(path, data):
        real(path, data)
        if path.name == "decision_tape.csv":
            raise KeyboardInterrupt
    monkeypatch.setattr(rb, "_write", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _export(tmp_path)
    assert not (tmp_path / "out").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def _resum(out):
    files = {n: (out / n).read_bytes() for n in rb.OUTPUT_FILES}
    (out / "SHA256SUMS").write_bytes(rb.sha256sums(files))


def test_negative_control_missing_receipt(tmp_path):
    out, _ = _export(tmp_path)
    (out / "bridge_receipt.json").unlink()
    with pytest.raises(rb.TapeError, match="missing"):
        rb.verify(out)


def test_negative_control_mismatched_receipt(tmp_path):
    out, r = _export(tmp_path)
    (out / "bridge_receipt.json").write_text(json.dumps({**r, "csv_sha256": "0" * 64}))
    with pytest.raises(rb.TapeError, match="SHA256SUMS"):
        rb.verify(out)
    _resum(out)                                         # even with SHA256SUMS regenerated, the receipt disagrees
    with pytest.raises(rb.TapeError, match="csv_sha256"):
        rb.verify(out)


def test_negative_control_receipt_from_another_run(tmp_path):
    out, r = _export(tmp_path)
    (out / "bridge_receipt.json").write_text(json.dumps({**r, "rows": 390}))
    _resum(out)
    with pytest.raises(rb.TapeError, match="counts"):
        rb.verify(out)


def test_negative_control_tampered_csv_and_stripped_label(tmp_path):
    out, _ = _export(tmp_path)
    data = (out / "decision_tape.csv").read_bytes()
    (out / "decision_tape.csv").write_bytes(data.replace(b"100.1", b"100.2", 1))
    with pytest.raises(rb.TapeError):
        rb.verify(out)
    out2, r2 = _export(tmp_path, "out2")
    lab = (out2 / "decision_tape_labeled.csv").read_bytes().replace(b",PAPER_ONLY,", b",,", 1)
    (out2 / "decision_tape_labeled.csv").write_bytes(lab)
    (out2 / "bridge_receipt.json").write_text(json.dumps({**r2, "labeled_csv_sha256": sha(lab)}))
    _resum(out2)
    with pytest.raises(rb.TapeError, match="labels"):
        rb.verify(out2)


def test_negative_control_extra_file(tmp_path):
    out, _ = _export(tmp_path)
    (out / "fills.csv").write_text("x\n")
    with pytest.raises(rb.TapeError, match="extra"):
        rb.verify(out)


def test_input_receipt_member_missing(tmp_path):
    p = tmp_path / "TEST-FIXTURE-no-receipt.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(rb.BASE + "decisions.json", RAW)
    with pytest.raises(rb.TapeError, match="member missing"):
        rb.convert(p, POLICY, archive_sha=sha(p.read_bytes()))


def test_input_receipt_mismatch():
    with pytest.raises(rb.TapeError, match="frozen receipt"):
        rb.project(RAW, {**RECEIPT, "decisions_sha256": "0" * 64}, POLICY, fixture_sha="TEST-FIXTURE")
    with pytest.raises(rb.TapeError, match="count"):
        rb.project(RAW, {**RECEIPT, "count": 390}, POLICY, fixture_sha="TEST-FIXTURE")


# ------------------------------------------------------------------------------- fix 4: guard, folder, price
def test_network_guard_active_and_bridge_runs_under_it(tmp_path):
    """In one fresh process with the repo network guard on: a loopback connection is refused by the guard, and the
    bridge still exports (it needs no network)."""
    bundle, asha = make_bundle(tmp_path)
    code = ("import socket, sys\nfrom optpipe.research import replay_bridge as rb\n"
            "try:\n    socket.create_connection(('127.0.0.1', 9), timeout=0.5)\n    sys.exit(97)\n"
            "except BaseException as e:\n    assert 'network guard' in str(e), e\n"
            f"sys.exit(rb.main(['export', {str(bundle)!r}, {str(tmp_path / 'out')!r}], _archive_sha={asha!r}))")
    log = tmp_path / "netguard.log"
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False,
                         env={**os.environ, "OPTPIPE_NETGUARD": "1", "OPTPIPE_NETGUARD_LOG": str(log)})
    assert run.returncode == 0, run.stderr
    hits = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert len(hits) == 1 and "create_connection" in hits[0], hits    # only the probe; the bridge made no call
    assert rb.verify(tmp_path / "out")["rows"] == 391


def test_bridge_imports_no_network_module():
    tree = ast.parse(Path(rb.__file__).read_text())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods <= {"__future__", "argparse", "csv", "hashlib", "io", "json", "os", "platform", "shlex", "shutil",
                    "sys", "tempfile", "zipfile", "datetime", "decimal", "pathlib", "optpipe"}, mods


@pytest.mark.parametrize("prefill", ["empty", "file"])
def test_existing_output_folder_refused(tmp_path, prefill):
    bundle, asha = make_bundle(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    if prefill == "file":
        (out / "keep.txt").write_text("TEST FIXTURE")
    run = cli(["export", bundle, out], asha)
    assert run.returncode == 2 and "already exists" in run.stderr
    assert sorted(p.name for p in out.iterdir()) == ([] if prefill == "empty" else ["keep.txt"])
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_signal_with_missing_price_is_blocked_not_promoted():
    t = tape()
    t[S]["indicators"]["price"].update(status="missing", value=None, as_of=None)
    files, s = run_project(t, price_kind="bar_close_1m")
    lab = rows(files["decision_tape_labeled.csv"])[S]
    assert (lab["upstream_decision"], lab["decision"], lab["bull_signal"], lab["observed_price"]) == \
        ("SIGNAL_BULL", "BLOCKED", "0", "")
    assert s["counts"]["SIGNAL_BULL"] == 0
    assert [r["reason_code"] for r in rows(files["price_block_log.csv"])] == ["PF-STATUS", "PF-STATUS"]


def test_cli_wrong_bundle_no_output(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not the frozen bundle")
    run = cli(["export", bad, tmp_path / "out"], rb.ARCHIVE_SHA)
    assert run.returncode == 2 and not (tmp_path / "out").exists()


# ------------------------------------------------------------------------------------------- fix 7: policy fail-closed
def test_policy_missing_fails_closed(tmp_path):
    bundle, asha = make_bundle(tmp_path)
    run = cli(["export", bundle, tmp_path / "out", "--price-policy", tmp_path / "nope.json"], asha)
    assert run.returncode == 2 and "not configured" in run.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("patch", [
    {"bar_close_1m": None}, {"bar_close_1m": {"max_age_seconds": None}}, {"quote_last": {}},
    {"future_tolerance_seconds": None}, {"future_tolerance_seconds": True}, {"policy_id": ""},
    {"bar_close_1m": {"max_age_seconds": "-1"}}, {"quote_last": {"max_age_seconds": "NaN"}},
])
def test_policy_unset_value_fails_closed(patch):
    raw = {**json.loads((ROOT / "config" / "replay_bridge_price_policy.json").read_text()), **patch}
    with pytest.raises(rb.PolicyError):
        rb.load_policy(raw)


def test_policy_values_are_adjustable():
    loose = rb.load_policy({"policy_id": "TEST-FIXTURE", "bar_close_1m": {"max_age_seconds": "60"},
                            "quote_last": {"max_age_seconds": "900"}, "future_tolerance_seconds": "60"})
    assert evaluate("bar", "2026-11-20T09:59:00-05:00", policy=loose)["ok"]          # PF-03 passes at 60 s
    assert evaluate("bar", "2026-11-20T10:00:30-05:00", policy=loose)["ok"]          # future within 60 s


def test_policy_file_is_proposed_and_matches_doodle_defaults():
    raw = json.loads((ROOT / "config" / "replay_bridge_price_policy.json").read_text())
    assert raw["status"] == "PROPOSED"
    assert "fc50b0e1d803fbe9b3f376beb05a7a88a1cc4cec2bb76189f44f60b4f2547619" in raw["source"]
    assert POLICY["max_age_seconds"] == {"bar_close_1m": 0, "quote_last": 900}
    assert POLICY["future_tolerance_seconds"] == 0


# ------------------------------------------------------------------------------- PF-01..PF-34 (TEST FIXTURE)
D = "2026-11-20T10:00:00-05:00"
MODES = {"bar": "bar_close_1m", "quote": "quote_last"}


def evaluate(mode, as_of=D, decision=D, policy=POLICY, **price):
    p = {"status": "ok", "value": "102", "source_id": TF, "as_of": as_of, **price}
    if as_of is ...:
        del p["as_of"]
    return rb.evaluate_price(p, datetime.fromisoformat(decision), MODES.get(mode, mode), policy)


def blocked(res, code, age=...):
    assert not res["ok"] and res["reason"] == code, res
    if age is not ...:
        assert res["age"] == (None if age is None else Decimal(str(age))), res


PF = {   # id: (mode, kwargs, expected reason or None for OK, expected age or ...)
    "PF-01": ("bar", {"as_of": D}, None, 0),
    "PF-02": ("bar", {"as_of": "2026-11-20T09:59:59-05:00"}, "PF-STALE", 1),
    "PF-03": ("bar", {"as_of": "2026-11-20T09:59:00-05:00"}, "PF-STALE", 60),
    "PF-04": ("quote", {"as_of": "2026-11-20T09:45:00-05:00"}, None, 900),
    "PF-05": ("quote", {"as_of": "2026-11-20T09:44:59-05:00"}, "PF-STALE", 901),
    "PF-06": ("quote", {"as_of": "2026-11-20T09:44:59.999-05:00"}, "PF-STALE", "900.001"),
    "PF-07": ("bar", {"as_of": "2026-11-20T10:00:01-05:00"}, "PF-FUTURE", -1),
    "PF-08": ("bar", {"as_of": None}, "PF-TIME-MISSING", None),
    "PF-09a": ("bar", {"as_of": ...}, "PF-TIME-MISSING", None),
    "PF-09b": ("bar", {"as_of": ""}, "PF-TIME-MISSING", None),
    "PF-10": ("bar", {"as_of": "2026-11-20T10:00:00"}, "PF-TIME-NAIVE", None),
    "PF-11": ("bar", {"as_of": "yesterday"}, "PF-TIME-INVALID", None),
    "PF-12": ("bar", {"status": "stale"}, "PF-STATUS", 0),
    "PF-13": ("bar", {"status": "missing", "value": None, "as_of": None}, "PF-STATUS", None),
    "PF-14": ("bar", {"status": "conflicting"}, "PF-STATUS", 0),
    "PF-15": ("bar", {"status": "OK"}, "PF-STATUS", 0),
    "PF-16a": ("bar", {"value": "NaN"}, "PF-VALUE", 0),
    "PF-16b": ("bar", {"value": "Infinity"}, "PF-VALUE", 0),
    "PF-16c": ("bar", {"value": "0"}, "PF-VALUE", 0),
    "PF-16d": ("bar", {"value": "-1"}, "PF-VALUE", 0),
    "PF-16e": ("bar", {"value": None}, "PF-VALUE", 0),
    "PF-17": ("quote", {"bid": "101.9", "ask": "101.8"}, "PF-BIDASK", 0),
    "PF-18": ("quote", {"bid": "0", "ask": "0.05"}, "PF-BIDASK", 0),
    "PF-19a": ("quote", {"bid": "101.9"}, "PF-BIDASK", 0),
    "PF-19b": ("quote", {"bid": "101.9", "ask": "NaN"}, "PF-BIDASK", 0),
    "PF-20": ("quote", {"bid": "101.9", "ask": "101.9"}, None, 0),
    "PF-23": ("quote", {"decision": "2026-11-20T09:31:00-05:00", "as_of": "2026-11-19T15:59:00-05:00"},
              "PF-SESSION", 63120),
    "PF-24": ("bar", {"decision": "2026-11-21T10:00:00-05:00", "as_of": "2026-11-21T10:00:00-05:00"}, "PF-CLOSED", 0),
    "PF-25": ("bar", {"decision": "2026-11-26T10:00:00-05:00", "as_of": "2026-11-26T10:00:00-05:00"}, "PF-CLOSED", 0),
    "PF-26": ("bar", {"decision": "2026-11-27T12:59:00-05:00", "as_of": "2026-11-27T12:59:00-05:00"}, None, 0),
    "PF-27": ("bar", {"decision": "2026-11-27T13:00:00-05:00", "as_of": "2026-11-27T13:00:00-05:00"}, None, 0),
    "PF-28": ("bar", {"decision": "2026-11-27T13:01:00-05:00", "as_of": "2026-11-27T13:01:00-05:00"}, "PF-CLOSED", 0),
    "PF-29": ("bar", {"decision": "2026-11-20T09:29:59-05:00", "as_of": "2026-11-20T09:29:59-05:00"}, "PF-CLOSED", 0),
    "PF-30": ("quote", {"decision": "2026-10-26T14:35:00+01:00", "as_of": "2026-10-26T09:34:00-04:00"}, None, 60),
    "PF-31": ("quote", {"decision": "2026-11-01T01:30:00-05:00", "as_of": "2026-11-01T01:30:00-04:00"},
              "PF-CLOSED", 3600),
    "PF-32": ("quote", {"decision": "2026-11-02T09:31:00-05:00", "as_of": "2026-10-30T15:59:00-04:00"},
              "PF-SESSION", 239520),
    "PF-33": ("bar", {"decision": "2028-01-04T10:00:00-05:00", "as_of": "2028-01-04T10:00:00-05:00"}, "PF-CALENDAR", 0),
    "PF-34": (None, {}, "PF-KIND", 0),
}


@pytest.mark.parametrize("pf", sorted(PF))
def test_pf_price_freshness_cases(pf):
    """TEST FIXTURE (SYNTHETIC) cases from Doodle's PROPOSED policy, section 4."""
    mode, kw, code, age = PF[pf]
    res = evaluate(mode, **kw)
    if code is None:
        assert res["ok"] and res["reason"] is None, res
        assert res["age"] == Decimal(str(age))
    else:
        blocked(res, code, age)


def test_pf_never_raises_on_nulls():
    for price in ({}, {"status": None}, {"status": "ok"}, {"status": "ok", "as_of": D},
                  {"status": "ok", "as_of": 5, "value": 5}, {"status": "ok", "as_of": D, "value": [1]}):
        assert not rb.evaluate_price(price, datetime.fromisoformat(D), "bar_close_1m", POLICY)["ok"]


def test_pf02_through_export_downgrades_signal_and_logs():
    t = tape()
    t[S]["indicators"]["price"]["as_of"] = "2026-11-20T09:59:59-05:00"
    files, _s = run_project(t, price_kind="bar_close_1m")
    lab = rows(files["decision_tape_labeled.csv"])[S]
    assert (lab["decision"], lab["bull_signal"], lab["observed_price"], lab["price_reason_code"],
            lab["price_age_seconds"]) == ("BLOCKED", "0", "", "PF-STALE", "1")
    log = rows(files["price_block_log.csv"])
    assert len(log) == 2 and log[1]["upstream_action"] == "SIGNAL_BULL" and log[1]["final_action"] == "BLOCKED"


def test_pf21_abstain_row_not_promoted():
    t = tape()
    t[100]["indicators"]["price"]["as_of"] = "2026-11-20T11:09:59-05:00"
    files, _s = run_project(t, price_kind="bar_close_1m")
    r = rows(files["price_block_log.csv"])[1]
    assert (r["upstream_action"], r["final_action"], r["reason_code"]) == ("ABSTAIN", "ABSTAIN", "PF-STALE")
    assert rows(files["decision_tape.csv"])[100]["observed_price"] == ""


def test_pf22_no_forward_fill():
    t = tape()
    t[101]["indicators"]["price"].update(status="missing", value=None, as_of=None)
    files, _ = run_project(t, price_kind="bar_close_1m")
    tp = rows(files["decision_tape.csv"])
    assert tp[100]["observed_price"] != "" and tp[101]["observed_price"] == "" and tp[101]["observed_price_at_ny"] == ""


def test_pf34_non_frozen_tape_without_price_kind_blocks_every_price():
    t = tape()
    t[200]["explanation"] += " TEST FIXTURE"
    _files, s = run_project(t)                                   # no price_kind: not the frozen decisions bytes
    assert s["price_blocks"]["by_reason"] == {"PF-KIND": 391}
    assert s["counts"]["SIGNAL_BULL"] == 0


def test_pf36_rows_in_equal_rows_out_and_log_kind():
    t = tape()
    for i in (40, 41, 42):
        t[i]["indicators"]["price"]["status"] = "stale"
    files, s = run_project(t, price_kind="bar_close_1m")
    lab, log = rows(files["decision_tape_labeled.csv"]), rows(files["price_block_log.csv"])
    assert len(lab) == len(t) == s["rows"]
    assert len(log) == sum(r["price_status"] == "BLOCKED" for r in lab) == 4
    assert {r["kind"] for r in log} == {"SYNTHETIC"}


@pytest.mark.parametrize("mut", [
    lambda t: t[S]["clock"].__setitem__("ny", "2026-11-20T10:00:00"),
    lambda t: t.__setitem__(31, copy.deepcopy(t[30])),
    lambda t: t[S]["indicators"]["price"].__setitem__("source_id", "TEST-FIXTURE-other"),
])
def test_pf37_integrity_failures_still_abort_with_no_output(tmp_path, mut):
    t = tape()
    mut(t)
    raw = json.dumps(t).encode()
    bundle, asha = make_bundle(tmp_path, raw, {**RECEIPT, "decisions_sha256": sha(raw)})
    run = cli(["export", bundle, tmp_path / "out"], asha)
    assert run.returncode == 2 and not (tmp_path / "out").exists(), run.stderr


def test_pf38_changed_signal_price_with_stale_receipt(tmp_path):
    raw = RAW.replace(b'"102"', b'"103"', 1)
    bundle, asha = make_bundle(tmp_path, raw)
    run = cli(["export", bundle, tmp_path / "out"], asha)
    assert run.returncode == 2 and "decisions do not match frozen receipt" in run.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mut", [
    lambda t: t.pop(),                                                     # last row (16:00) dropped
    lambda t: t.pop(0),                                                    # first row (09:30) dropped
    lambda t: t[S]["indicators"].__setitem__("iv_rank", {"as_of": D, "source_id": t[S]["provenance"]["source_id"],
                                                         "status": "ok", "value": "42"}),   # well-formed but invented
    lambda t: t[S]["indicators"]["vwap"].__setitem__("note", "TEST FIXTURE"),             # extra key on an input
])
def test_session_coverage_and_indicator_shape(mut):
    t = tape()
    mut(t)
    with pytest.raises(rb.TapeError):
        run_project(t)
