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
RAW_POLICY = json.loads((ROOT / "config" / "replay_bridge_price_policy.json").read_text())
POLICY = rb.load_policy(RAW_POLICY)
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
    assert s["price_blocks"] == {"total": 1, "by_reason": {"PF-STATUS": 1}, "by_label": {"BAR": 1}}
    assert s["price_accepts"] == {"total": 390, "by_label": {"BAR": 390}}
    acc = rows(files["price_accept_log.csv"])
    assert len(acc) == 390 and {(r["label"], r["age_seconds"], r["price_kind"]) for r in acc} == \
        {("BAR", "0", "bar_close_1m")}
    lab = rows(files["decision_tape_labeled.csv"])
    assert sum(r["price_status"] == "OK" and r["price_age_seconds"] == "0" and r["price_label"] == "BAR"
               for r in lab) == 390
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



FIELDS_DECISION = rb.FIELDS.index("decision")


def _resign(out):
    files = {n: (out / n).read_bytes() for n in rb.OUTPUT_FILES}
    (out / "SHA256SUMS").write_bytes(rb.sha256sums(files))


def _edit_receipt(out, mut):
    rec = json.loads((out / "bridge_receipt.json").read_text())
    mut(rec)
    (out / "bridge_receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    _resign(out)


def test_receipt_records_code_hash_command_timestamp_and_sha256sums(tmp_path):
    out, r = _export(tmp_path)
    assert sorted(p.name for p in out.iterdir()) == sorted(rb.OUTPUT_FILES + ("SHA256SUMS",))
    assert r["code_sha256"] == sha(Path(rb.__file__).read_bytes())
    assert r["command"] == "python -m optpipe.research.replay_bridge export b out"
    assert r["generated_at_utc"] == "2026-11-28T13:00:00+00:00"
    assert r["price_policy"]["status"] == "PROPOSED"
    assert r["price_policy"]["max_age_seconds"] == {"bar_close_1m": "0", "quote_last": "900"}
    assert r["price_policy"]["path"] == "config/replay_bridge_price_policy.json"
    assert r["price_policy"]["file_sha256"] == sha((ROOT / "config" / "replay_bridge_price_policy.json").read_bytes())
    assert r["price_policy"]["status_note"] == rb.POLICY_STATUS_NOTE
    assert "not owner-approved" in r["price_policy"]["status_note"]
    assert "no approval mechanism" in r["price_policy"]["status_note"]
    sums = dict(reversed(line.split("  ")) for line in (out / "SHA256SUMS").read_text().splitlines())
    assert sums == {n: sha((out / n).read_bytes()) for n in rb.OUTPUT_FILES}
    assert json.loads((out / "bridge_receipt.json").read_bytes()) == r
    v = rb.verify(out)
    assert v["rows"] == 391 and v["_verification"]["mode"] == rb.VERIFY_SELF_ONLY
    assert v["_verification"]["checked"] == ["csv_sha256", "code_sha256", "policy_file_sha256"]
    assert v["_verification"]["skipped"] == ["archive_sha256", "decisions_sha256", "official_sidecars"]


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


def test_verify_without_pins_prints_self_consistency_only(tmp_path):
    out, _r = _export(tmp_path)
    _bundle, asha = make_bundle(tmp_path)
    run = cli(["verify", "--self-consistency-only", out], asha)
    assert run.returncode == 0, run.stderr
    assert "self-consistency only" in run.stdout
    assert "checked: csv_sha256, code_sha256, policy_file_sha256" in run.stdout
    assert "skipped: archive_sha256, decisions_sha256, official_sidecars" in run.stdout
    assert "Diagnostic sidecar fields" in run.stdout
    assert "Policy status PROPOSED is not owner-approved" in run.stdout


def test_verify_identity_pins_match(tmp_path):
    out, r = _export(tmp_path)
    _bundle, asha = make_bundle(tmp_path)
    args = ["verify", "--self-consistency-only", out,
            "--expect-archive-sha256", r["archive_sha256"],
            "--expect-decisions-sha256", r["decisions_sha256"],
            "--expect-code-sha256", r["code_sha256"],
            "--expect-policy-sha256", r["price_policy"]["file_sha256"]]
    run = cli(args, asha)
    assert run.returncode == 0, run.stderr
    assert "self-consistency plus supplied identity pins" in run.stdout
    assert "checked:" in run.stdout
    for name in ("archive_sha256", "decisions_sha256", "csv_sha256", "code_sha256", "policy_file_sha256"):
        assert name in run.stdout
    assert "skipped: official_sidecars" in run.stdout
    assert "Policy status PROPOSED is not owner-approved" in run.stdout


@pytest.mark.parametrize("flag,field", [
    ("--expect-archive-sha256", "archive"),
    ("--expect-decisions-sha256", "decisions"),
    ("--expect-code-sha256", "code"),
    ("--expect-policy-sha256", "policy"),
])
def test_verify_identity_pin_mismatch_fails(tmp_path, flag, field):
    out, _r = _export(tmp_path)
    _bundle, asha = make_bundle(tmp_path)
    run = cli(["verify", "--self-consistency-only", out, flag, "0" * 64], asha)
    assert run.returncode == 2, run.stderr
    assert "does not match the expected value" in run.stderr
    # the folder itself is left untouched and is still self-consistent
    v = rb.verify(out)["_verification"]
    assert "archive_sha256" in v["skipped"]
    assert "policy_file_sha256" in v["checked"]
    assert "official_sidecars" in v["skipped"]
    assert "code_sha256" in v["checked"]


def _rehash_fixture(out, receipt, new_sha):
    """Rebuild a self-consistent folder whose fixture / archive hash is `new_sha` (wrong)."""
    old = receipt["labels"]["fixture_set_sha256"]
    for name in ("decision_tape_labeled.csv", "price_block_log.csv", "price_accept_log.csv"):
        (out / name).write_bytes((out / name).read_bytes().replace(old.encode(), new_sha.encode()))
    r = json.loads((out / "bridge_receipt.json").read_text())
    r["labels"]["fixture_set_sha256"] = new_sha
    r["archive_sha256"] = new_sha
    for key, name in (("labeled_csv_sha256", "decision_tape_labeled.csv"),
                      ("price_block_log_sha256", "price_block_log.csv"),
                      ("price_accept_log_sha256", "price_accept_log.csv")):
        r[key] = sha((out / name).read_bytes())
    (out / "bridge_receipt.json").write_text(json.dumps(r, sort_keys=True, indent=2) + "\n")
    _resum(out)
    return r


def test_verify_rehashed_wrong_fixture_is_self_consistent_but_pin_fails(tmp_path):
    """A folder rebuilt so its own SUMS and receipt agree, but the fixture hash is not the real one:
    verify() without pins passes; --expect-archive-sha256 of the real fixture fails."""
    out, r = _export(tmp_path)
    real = r["archive_sha256"]
    wrong = "ab" * 32
    assert wrong != real
    _rehash_fixture(out, r, wrong)
    v = rb.verify(out)
    assert v["_verification"]["mode"] == rb.VERIFY_SELF_ONLY
    assert "archive_sha256" in v["_verification"]["skipped"]
    assert v["archive_sha256"] == wrong
    _bundle, asha = make_bundle(tmp_path)
    run = cli(["verify", "--self-consistency-only", out], asha)
    assert run.returncode == 0, run.stderr
    assert "self-consistency only" in run.stdout
    run = cli(["verify", "--self-consistency-only", out, "--expect-archive-sha256", real], asha)
    assert run.returncode == 2
    assert "does not match the expected value" in run.stderr or "fixture_set_sha256" in run.stderr
    with pytest.raises(rb.TapeError, match="does not match"):
        rb.verify(out, expect_archive_sha256=real)
    with pytest.raises(rb.TapeError, match="does not match"):
        rb.verify(out, expect_decisions_sha256=real)   # wrong field on purpose: decisions sha is still the real tape
    # decisions pin of the real decisions sha still passes (we only changed the fixture/archive field)
    assert rb.verify(out, expect_decisions_sha256=r["decisions_sha256"])["decisions_sha256"] == r["decisions_sha256"]


def test_verify_pin_on_missing_receipt_field_fails(tmp_path):
    out, r = _export(tmp_path)
    del r["code_sha256"]
    (out / "bridge_receipt.json").write_text(json.dumps(r, sort_keys=True, indent=2) + "\n")
    _resum(out)
    with pytest.raises(rb.TapeError, match="code_sha256"):
        rb.verify(out, expect_code_sha256="0" * 64)


def test_policy_status_is_recorded_as_declared_with_no_approval_mechanism(tmp_path):
    raw = {**RAW_POLICY, "status": "APPROVED"}   # a file may claim anything; this is not an approval
    path = tmp_path / "TEST-FIXTURE-policy.json"
    path.write_text(json.dumps(raw))
    out, r = _export(tmp_path, "out-declared", policy_path=path)
    assert r["price_policy"]["status"] == "APPROVED"
    assert r["price_policy"]["path"] == path.name          # outside the repo: basename only, no absolute path
    assert r["price_policy"]["file_sha256"] == sha(path.read_bytes())
    assert "no approval mechanism" in r["price_policy"]["status_note"]
    assert "not owner-approved" in r["price_policy"]["status_note"]
    _bundle, asha = make_bundle(tmp_path)
    run = cli(["verify", "--self-consistency-only", out], asha)
    assert run.returncode == 2, run.stderr
    assert "policy" in run.stderr
    # Approval status is never read from the receipt.
    with pytest.raises(rb.TapeError, match="policy"):
        rb.verify(out)


def test_export_stdout_states_proposed_is_not_owner_approved(tmp_path):
    bundle, asha = make_bundle(tmp_path)
    run = cli(["export", bundle, tmp_path / "out"], asha)
    assert run.returncode == 0, run.stderr
    assert "Policy status PROPOSED is not owner-approved" in run.stdout


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
    assert mods <= {"__future__", "argparse", "csv", "hashlib", "io", "json", "math", "os", "platform", "signal", "shlex",
                    "shutil", "sys", "tempfile", "zipfile", "datetime", "decimal", "pathlib", "optpipe"}, mods


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
    {"delayed_quote": None}, {"delayed_quote": {}}, {"delayed_quote": {"max_declared_delay_seconds": "900"}},
    {"delayed_quote": {"max_declared_delay_seconds": "900", "absolute_ceiling_seconds": None,
                       "adjusted_max_seconds": "300"}},
    {"delayed_quote": {"max_declared_delay_seconds": "30", "absolute_ceiling_seconds": "1200",
                       "adjusted_max_seconds": "300"}},
])
def test_policy_unset_value_fails_closed(patch):
    raw = {**json.loads((ROOT / "config" / "replay_bridge_price_policy.json").read_text()), **patch}
    with pytest.raises(rb.PolicyError):
        rb.load_policy(raw)


def test_policy_values_are_adjustable():
    loose = rb.load_policy({**RAW_POLICY, "policy_id": "TEST-FIXTURE", "bar_close_1m": {"max_age_seconds": "60"},
                            "future_tolerance_seconds": "60"})
    assert evaluate("bar", "2026-11-20T09:59:00-05:00", policy=loose)["ok"]          # PF-03 passes at 60 s
    assert evaluate("bar", "2026-11-20T10:00:30-05:00", policy=loose)["ok"]          # future within 60 s


def test_policy_file_is_proposed_and_matches_doodle_defaults():
    raw = json.loads((ROOT / "config" / "replay_bridge_price_policy.json").read_text())
    assert raw["status"] == "PROPOSED"
    assert raw["source"].startswith("trading/proposals/ReplayBridge-001-price-freshness.md sha256 "
                                    "805f2681eca44e2cc45b586861da10be84aa3067817cdd4c000fc90076e3e246")
    assert "relayed by Soodle" in raw["owner"]
    assert POLICY["max_age_seconds"] == {"bar_close_1m": 0, "quote_last": 900}
    assert POLICY["future_tolerance_seconds"] == 0
    assert POLICY["delayed_quote"] == {"max_declared_delay_seconds": 900, "absolute_ceiling_seconds": 1200,
                                       "adjusted_max_seconds": 300}


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
    acc = rows(files["price_accept_log.csv"])
    assert len(lab) == len(t) == s["rows"]
    assert len(log) == sum(r["price_status"] == "BLOCKED" for r in lab) == 4
    assert len(log) + len(acc) == len(lab)                       # every row is either accepted or blocked, none dropped
    assert {r["kind"] for r in log + acc} == {"SYNTHETIC"}


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


# ------------------------------------------------------------------------------- PF-39..PF-46 DELAYED (TEST FIXTURE)
def dq(minutes=15, **kw):
    return {"delayed": True, "delay_minutes": minutes, **kw}


PF_DELAYED = {   # id: (mode, kwargs, expected reason or None, label, age, delay, adjusted)
    "PF-39": ("quote", dq(as_of="2026-11-20T09:45:00-05:00"), None, "DELAYED", 900, 900, 0),
    "PF-40": ("quote", dq(as_of="2026-11-20T09:40:00-05:00"), None, "DELAYED", 1200, 900, 300),
    "PF-41": ("quote", dq(as_of="2026-11-20T09:39:59-05:00"), "PF-STALE", "DELAYED", 1201, 900, 301),
    "PF-42": ("quote", dq(10, as_of="2026-11-20T09:44:59-05:00"), "PF-STALE", "DELAYED", 901, 600, 301),
    "PF-43a": ("quote", {"delayed": True, "delay_seconds": 901}, "PF-DELAY-DECL", "DELAYED", 0, None, None),
    "PF-43b": ("quote", dq(16), "PF-DELAY-DECL", "DELAYED", 0, None, None),
    "PF-44": ("quote", dq(as_of="2026-11-20T09:45:01-05:00"), "PF-DELAY-DECL", "DELAYED", 899, 900, -1),
    "PF-45": ("quote", {"as_of": "2026-11-20T09:44:59-05:00"}, "PF-STALE", "REALTIME", 901, None, None),
    "PF-46": ("bar", dq(), "PF-KIND", "BAR", 0, None, None),
}


@pytest.mark.parametrize("pf", sorted(PF_DELAYED))
def test_pf_delayed_cases(pf):
    """TEST FIXTURE (SYNTHETIC) PF-39..PF-46 from Doodle's PROPOSED policy (805f2681...e246)."""
    mode, kw, code, label, age, delay, adjusted = PF_DELAYED[pf]
    res = evaluate(mode, **kw)
    assert (res["ok"], res["reason"]) == (code is None, code), res
    assert res["label"] == label and res["age"] == Decimal(age)
    assert res["delay"] == (None if delay is None else Decimal(delay))
    assert res["adjusted"] == (None if adjusted is None else Decimal(adjusted))


@pytest.mark.parametrize("decl", [
    {"delayed": True},                                                    # no delay at all
    {"delayed": True, "delay_minutes": 0}, {"delayed": True, "delay_minutes": -1},
    {"delayed": True, "delay_minutes": 15.0}, {"delayed": True, "delay_minutes": "15"},
    {"delayed": True, "delay_minutes": True}, {"delayed": True, "delay_minutes": None},
    {"delayed": True, "delay_seconds": 0}, {"delayed": True, "delay_seconds": 30},
    {"delayed": True, "delay_seconds": 960}, {"delayed": True, "delay_seconds": float("nan")},
    {"delayed": True, "delay_seconds": float("inf")}, {"delayed": True, "delay_seconds": 59.5},
    {"delayed": True, "delay_minutes": 5, "delay_seconds": 600},          # both present and disagreeing
    {"delayed": False, "delay_minutes": 15},                              # a delay field without delayed:true
    {"delayed": "true", "delay_minutes": 15}, {"delayed": 1, "delay_minutes": 15},
    {"delay_minutes": 15}, {"delay_seconds": 900},
])
def test_pf_bad_delay_declaration_is_blocked_as_delayed(decl):
    res = evaluate("quote", as_of="2026-11-20T09:50:00-05:00", **decl)
    assert (res["ok"], res["reason"], res["label"]) == (False, "PF-DELAY-DECL", "DELAYED"), res


@pytest.mark.parametrize("decl", [
    {"delayed": True, "delay_minutes": 1}, {"delayed": True, "delay_minutes": 15},
    {"delayed": True, "delay_seconds": 60},
    {"delayed": True, "delay_seconds": 900}, {"delayed": True, "delay_minutes": 5, "delay_seconds": 300},
    {"delayed": True, "delay_seconds": 300.0},
])
def test_pf_good_delay_declarations_are_delayed(decl):
    delay = decl.get("delay_minutes", 0) * 60 or decl["delay_seconds"]
    as_of = datetime.fromisoformat(D).timestamp() - delay
    iso = datetime.fromtimestamp(as_of, tz=datetime.fromisoformat(D).tzinfo).isoformat()
    res = evaluate("quote", as_of=iso, **decl)
    assert res["ok"] and res["label"] == "DELAYED" and res["adjusted"] == 0, res


def test_pf_delayed_false_is_realtime():
    res = evaluate("quote", delayed=False)
    assert res["ok"] and res["label"] == "REALTIME" and res["delay"] is None and res["adjusted"] is None
    res = evaluate("quote", as_of="2026-11-20T09:44:59-05:00", delayed=False)         # no allowance without delay
    assert (res["reason"], res["label"]) == ("PF-STALE", "REALTIME")


@pytest.mark.parametrize("field", [{"delayed": True}, {"delayed": False}, {"delay_minutes": 15}, {"delay_seconds": 900},
                                   {"delayed": "false"}])
def test_pf_bar_with_any_delay_field_is_pf_kind(field):
    res = evaluate("bar", **field)
    assert (res["reason"], res["label"]) == ("PF-KIND", "BAR"), res


def test_pf_delayed_check_order():
    """First failure wins, in Doodle's order: KIND, STATUS, TIME-*, CALENDAR, CLOSED, FUTURE, SESSION, DELAY-DECL
    (declaration, then adjusted < 0), STALE, VALUE, BIDASK."""
    bad = {"delayed": True, "delay_minutes": 99}
    assert evaluate("bar", status="stale", **bad)["reason"] == "PF-KIND"
    assert evaluate("quote", status="stale", **bad)["reason"] == "PF-STATUS"
    assert evaluate("quote", as_of=None, **bad)["reason"] == "PF-TIME-MISSING"
    assert evaluate("quote", as_of="2026-11-20T10:00:00", **bad)["reason"] == "PF-TIME-NAIVE"
    assert evaluate("quote", decision="2026-11-21T10:00:00-05:00", as_of="2026-11-21T10:00:00-05:00",
                    **bad)["reason"] == "PF-CLOSED"
    assert evaluate("quote", as_of="2026-11-20T10:00:01-05:00", **bad)["reason"] == "PF-FUTURE"
    assert evaluate("quote", decision="2026-11-20T09:31:00-05:00", as_of="2026-11-19T15:59:00-05:00",
                    **bad)["reason"] == "PF-SESSION"
    assert evaluate("quote", as_of="2026-11-20T09:30:00-05:00", **bad)["reason"] == "PF-DELAY-DECL"
    assert evaluate("quote", as_of="2026-11-20T09:39:59-05:00", value="NaN", **dq())["reason"] == "PF-STALE"
    assert evaluate("quote", value="NaN", bid="2", ask="1")["reason"] == "PF-VALUE"
    assert evaluate("quote", bid="2", ask="1")["reason"] == "PF-BIDASK"


def test_pf_delayed_policy_values_are_adjustable():
    wide = rb.load_policy({**RAW_POLICY, "delayed_quote": {"max_declared_delay_seconds": "1800",
                                                          "absolute_ceiling_seconds": "2400",
                                                          "adjusted_max_seconds": "600"}})
    assert not evaluate("quote", as_of="2026-11-20T09:30:00-05:00", **dq(30))["ok"]    # 30 min declared: still default
    assert evaluate("quote", as_of="2026-11-20T09:30:00-05:00", policy=wide, **dq(30))["ok"]
    assert evaluate("quote", decision="2026-11-20T15:00:00-05:00", as_of="2026-11-20T12:00:00-05:00", policy=wide,
                    **dq(30))["reason"] == "PF-STALE"


def test_pf39_through_export_delayed_signal_kept_and_logged_with_all_four_fields():
    t = tape()
    price = t[S]["indicators"]["price"]
    price.update(dq(as_of="2026-11-20T09:45:00-05:00"))
    files, s = run_project(t, price_kind="quote_last")
    lab = rows(files["decision_tape_labeled.csv"])[S]
    assert (lab["decision"], lab["bull_signal"], lab["price_label"], lab["price_delay_seconds"],
            lab["price_age_seconds"], lab["price_adjusted_age_seconds"]) == ("SIGNAL_BULL", "1", "DELAYED", "900",
                                                                             "900", "0")
    acc = [r for r in rows(files["price_accept_log.csv"]) if r["row_index"] == str(S)]
    assert [(r["label"], r["delay_seconds"], r["age_seconds"], r["adjusted_age_seconds"], r["action"])
            for r in acc] == [("DELAYED", "900", "900", "0", "SIGNAL_BULL")]
    assert s["price_accepts"]["by_label"]["DELAYED"] == 1
    # the tape row carries the observed price like any other accepted price; the label lives in the sidecars only
    assert rows(files["decision_tape.csv"])[S]["decision"] == "SIGNAL_BULL"
    assert set(rows(files["decision_tape.csv"])[S]) == set(rb.FIELDS)


def test_pf41_through_export_blocked_delayed_signal_becomes_blocked_and_is_not_filled():
    t = tape()
    t[S]["indicators"]["price"].update(dq(as_of="2026-11-20T09:39:59-05:00"))
    files, s = run_project(t, price_kind="quote_last")
    row = rows(files["decision_tape.csv"])[S]
    assert (row["decision"], row["bull_signal"], row["observed_price"], row["observed_price_at_ny"]) == \
        ("BLOCKED", "0", "", "")
    rec = [r for r in rows(files["price_block_log.csv"]) if r["row_index"] == str(S)]
    assert [(r["label"], r["reason_code"], r["age_seconds"], r["adjusted_age_seconds"], r["delay_seconds"],
             r["upstream_action"], r["final_action"]) for r in rec] == \
        [("DELAYED", "PF-STALE", "1201", "301", "900", "SIGNAL_BULL", "BLOCKED")]
    assert s["price_blocks"]["by_label"]["DELAYED"] == 1


def test_delayed_run_never_changes_the_frozen_tape_bytes():
    """The DELAYED class is a sidecar concern: the frozen archive still gives Sol's decision_tape.csv exactly."""
    assert sha(run_project()[0]["decision_tape.csv"]) == SOL_CSV_SHA


def test_pf_raw_ceiling_is_independent_of_the_adjusted_maximum():
    """With the default numbers the raw 1200 s ceiling is implied by delay <= 900 and adjusted <= 300. It is a separate
    parameter, so with a looser adjusted maximum it must still block on its own (reason PF-STALE, first match)."""
    loose = rb.load_policy({**RAW_POLICY, "delayed_quote": {"max_declared_delay_seconds": "900",
                                                           "absolute_ceiling_seconds": "1200",
                                                           "adjusted_max_seconds": "600"}})
    ok = evaluate("quote", as_of="2026-11-20T09:40:00-05:00", policy=loose, **dq())       # raw 1200, adjusted 300
    assert ok["ok"]
    res = evaluate("quote", as_of="2026-11-20T09:38:20-05:00", policy=loose, **dq())      # raw 1300, adjusted 400
    assert (res["reason"], res["age"], res["adjusted"]) == ("PF-STALE", Decimal(1300), Decimal(400))
    edge = evaluate("quote", as_of="2026-11-20T09:39:59-05:00", policy=loose, **dq())     # raw 1201, adjusted 301
    assert edge["reason"] == "PF-STALE"


# --- Zoodle PR #40 baseline (f0be2bc0): R13-R16, O3c, X2a/X2b/X2c, PP18, A6/A7 ---

def test_r13_edited_archive_hash_is_refused_after_resign(tmp_path):
    """R13: changing receipt archive_sha256 and regenerating SHA256SUMS is refused."""
    out, r = _export(tmp_path)
    original = r["archive_sha256"]
    _edit_receipt(out, lambda rec: rec.__setitem__("archive_sha256", "ab" * 32))
    with pytest.raises(rb.TapeError, match="archive_sha256"):
        rb.verify(out, expect_archive_sha256=original)
    with pytest.raises(rb.TapeError, match="archive_sha256"):
        rb.verify(out, pinned=True, expect_decisions_sha256=None)


def test_r14_edited_policy_numbers_are_refused_after_resign(tmp_path):
    """R14: changing receipt policy numbers (path still the default file) is refused."""
    out, _r = _export(tmp_path)
    def mut(rec):
        rec["price_policy"]["max_age_seconds"]["quote_last"] = "1"
    _edit_receipt(out, mut)
    with pytest.raises(rb.TapeError, match="price_policy"):
        rb.verify(out)
    with pytest.raises(rb.TapeError, match="price_policy"):
        rb.verify(out, expect_policy_sha256=rb.digest((ROOT / "config" / "replay_bridge_price_policy.json").read_bytes()))
    _bundle, asha = make_bundle(tmp_path)
    run = cli(["verify", "--self-consistency-only", out], asha)
    assert run.returncode == 2, run.stderr
    assert "price_policy" in run.stderr


def test_r15_edited_per_action_counts_are_refused_after_resign(tmp_path):
    """R15: receipt counts / block / accept breakdowns must match the CSVs (doc claim)."""
    out, r = _export(tmp_path)
    assert r["counts"]["SIGNAL_BEAR"] == 0
    def mut(rec):
        rec["counts"]["SIGNAL_BEAR"] = 1
        rec["counts"]["ABSTAIN"] = rec["counts"]["ABSTAIN"] - 1
    _edit_receipt(out, mut)
    with pytest.raises(rb.TapeError, match="per-action counts"):
        rb.verify(out)
    out2, _r = _export(tmp_path, name="out2")
    def mut_blocks(rec):
        rec["price_blocks"]["by_reason"] = {"PF-STATUS": 99}
    _edit_receipt(out2, mut_blocks)
    with pytest.raises(rb.TapeError, match="price_blocks"):
        rb.verify(out2)


def test_r16_edited_code_hash_is_refused_after_resign(tmp_path):
    """R16: changing receipt code_sha256 and regenerating SHA256SUMS is refused."""
    out, r = _export(tmp_path)
    original = r["code_sha256"]
    _edit_receipt(out, lambda rec: rec.__setitem__("code_sha256", "00" * 32))
    with pytest.raises(rb.TapeError, match="code_sha256"):
        rb.verify(out)
    with pytest.raises(rb.TapeError, match="code_sha256"):
        rb.verify(out, expect_code_sha256=original)


def test_o3c_plain_tape_is_cross_checked_against_labeled(tmp_path):
    """O3c: editing decision_tape.csv and re-signing it is refused against the labeled CSV."""
    out, _r = _export(tmp_path)
    path = out / "decision_tape.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    # flip the last HOLD/SIGNAL cell so the plain tape diverges from the labeled companion
    rows[-1][FIELDS_DECISION] = "SIGNAL_BEAR" if rows[-1][FIELDS_DECISION] != "SIGNAL_BEAR" else "HOLD"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    rec = json.loads((out / "bridge_receipt.json").read_text())
    rec["csv_sha256"] = rb.digest(path.read_bytes())
    (out / "bridge_receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    _resign(out)
    with pytest.raises(rb.TapeError, match="decision_tape.csv does not match decision_tape_labeled.csv"):
        rb.verify(out)


def test_x2a_forged_action_in_both_csvs_is_refused(tmp_path):
    """X2a: same forged action in both CSVs, receipt hashes/counts updated, SUMS regenerated."""
    out, r = _export(tmp_path)
    assert r["decisions_sha256"] == rb.FROZEN_DECISIONS_SHA
    target_ny = None
    for name in ("decision_tape.csv", "decision_tape_labeled.csv"):
        path = out / name
        rows = list(csv.reader(path.read_text().splitlines()))
        di = rows[0].index("decision")
        bull_i = rows[0].index("bull_signal")
        bear_i = rows[0].index("bear_signal")
        ny_i = rows[0].index("decision_at_ny")
        up_i = rows[0].index("upstream_decision") if "upstream_decision" in rows[0] else None
        for row in reversed(rows[1:]):
            if row[di] == "SIGNAL_BULL":
                row[di] = "SIGNAL_BEAR"
                row[bull_i] = "0"
                row[bear_i] = "1"
                if up_i is not None:
                    row[up_i] = "SIGNAL_BEAR"
                target_ny = row[ny_i]
                break
        with path.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)
    path = out / "price_accept_log.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    ai = rows[0].index("action")
    ny_i = rows[0].index("decision_at_ny")
    for row in rows[1:]:
        if row[ny_i] == target_ny:
            row[ai] = "SIGNAL_BEAR"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    rec = json.loads((out / "bridge_receipt.json").read_text())
    rec["csv_sha256"] = rb.digest((out / "decision_tape.csv").read_bytes())
    rec["labeled_csv_sha256"] = rb.digest((out / "decision_tape_labeled.csv").read_bytes())
    rec["price_accept_log_sha256"] = rb.digest((out / "price_accept_log.csv").read_bytes())
    rec["counts"] = {"ABSTAIN": rec["counts"]["ABSTAIN"], "SIGNAL_BULL": rec["counts"]["SIGNAL_BULL"] - 1,
                     "SIGNAL_BEAR": rec["counts"]["SIGNAL_BEAR"] + 1, "BLOCKED": rec["counts"]["BLOCKED"]}
    rec["upstream_counts"] = dict(rec["counts"])
    (out / "bridge_receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    _resign(out)
    with pytest.raises(rb.TapeError, match="does not match the frozen Lesson 001 projection"):
        rb.verify(out)


def test_x2b_official_fixture_relabel_is_refused(tmp_path):
    """X2b: relabel a test-bundle receipt as the official Lesson 001 v3 fixture and resign."""
    out, r = _export(tmp_path)
    assert r["labels"]["fixture_set"] == rb.UNOFFICIAL_FIXTURE_SET
    def mut(rec):
        rec["archive_sha256"] = "ab" * 32
        rec["labels"]["fixture_set"] = rb.FIXTURE_SET
        rec["labels"]["fixture_set_sha256"] = "ab" * 32
        for name in ("decision_tape_labeled.csv", "price_block_log.csv", "price_accept_log.csv"):
            pass
    _edit_receipt(out, mut)
    # also rewrite label columns in the CSVs so the label-row check does not fire first
    for name in ("decision_tape_labeled.csv", "price_block_log.csv", "price_accept_log.csv"):
        path = out / name
        rows = list(csv.reader(path.read_text().splitlines()))
        fi = rows[0].index("fixture_set")
        fsi = rows[0].index("fixture_set_sha256")
        for row in rows[1:]:
            row[fi] = rb.FIXTURE_SET
            row[fsi] = "ab" * 32
        with path.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)
    rec = json.loads((out / "bridge_receipt.json").read_text())
    rec["labeled_csv_sha256"] = rb.digest((out / "decision_tape_labeled.csv").read_bytes())
    rec["price_block_log_sha256"] = rb.digest((out / "price_block_log.csv").read_bytes())
    rec["price_accept_log_sha256"] = rb.digest((out / "price_accept_log.csv").read_bytes())
    (out / "bridge_receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    _resign(out)
    with pytest.raises(rb.TapeError, match="official Lesson 001 v3"):
        rb.verify(out)


def test_x2c_other_archive_same_tape_is_refused_against_the_pin(tmp_path):
    """X2c: a self-consistent folder from another archive sha (same frozen decisions/tape) fails pinned verify."""
    official, _real = make_bundle(tmp_path)
    repack = tmp_path / "repack.zip"
    with zipfile.ZipFile(official) as z, zipfile.ZipFile(repack, "w") as w:
        for info in z.infolist():
            w.writestr(info, z.read(info.filename))
        extra = zipfile.ZipInfo(rb.BASE + "README.txt", date_time=(2026, 11, 21, 16, 21, 0))
        w.writestr(extra, b"other pack")
    other = sha(repack.read_bytes())
    assert other != rb.ARCHIVE_SHA
    out = tmp_path / "out"
    r = rb.export(repack, out, ROOT / "config" / "replay_bridge_price_policy.json",
                  command="python -m optpipe.research.replay_bridge export b out", now=NOW, archive_sha=other)
    assert r["archive_sha256"] == other
    assert r["labels"]["fixture_set"] == rb.UNOFFICIAL_FIXTURE_SET
    assert r["decisions_sha256"] == rb.FROZEN_DECISIONS_SHA
    assert (out / "decision_tape.csv").read_bytes()  # same projection
    assert rb.digest((out / "decision_tape.csv").read_bytes()) == rb.FROZEN_CSV_SHA
    rb.verify(out)  # unpinned self-consistency of this other-archive folder is allowed
    with pytest.raises(rb.TapeError, match="archive_sha256"):
        rb.verify(out, pinned=True)
    with pytest.raises(rb.TapeError, match="archive_sha256"):
        rb.verify(out, expect_archive_sha256=rb.ARCHIVE_SHA)
    run = cli(["verify", out], other)
    assert run.returncode == 2
    assert "archive_sha256" in run.stderr


def test_pp18_string_policy_section_exits_2(tmp_path):
    """PP18: a policy section written as a string must exit 2, not crash with exit 1."""
    policy = tmp_path / "bad.json"
    raw = json.loads((ROOT / "config" / "replay_bridge_price_policy.json").read_text())
    raw["bar_close_1m"] = "0"
    policy.write_text(json.dumps(raw))
    bundle, asha = make_bundle(tmp_path)
    run = cli(["export", bundle, tmp_path / "out", "--price-policy", policy], asha)
    assert run.returncode == 2, run.stderr
    assert "AttributeError" not in run.stderr
    assert "Traceback" not in run.stderr
    assert "must be an object" in run.stderr


def test_a7_sigterm_cleans_up_the_temp_folder(tmp_path):
    """A7: SIGTERM during write_output removes the hidden partial folder and exits 2."""
    dest = tmp_path / "out"
    script = tmp_path / "kill_during_write.py"
    script.write_text(
        "import os, signal, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "from optpipe.research.replay_bridge import OUTPUT_FILES, PARTIAL_MARK, write_output\n"
        "real = os.rename\n"
        "def boom(src, dst):\n"
        "    if PARTIAL_MARK in Path(src).name:\n"
        "        os.kill(os.getpid(), signal.SIGTERM)\n"
        "    return real(src, dst)\n"
        "os.rename = boom\n"
        "files = {n: b'x' for n in OUTPUT_FILES}\n"
        "try:\n"
        f"    write_output(Path({str(dest)!r}), files)\n"
        "except SystemExit as e:\n"
        "    raise SystemExit(e.code)\n"
    )
    run = subprocess.run([sys.executable, str(script)], check=False, capture_output=True, text=True)
    assert run.returncode == 2, (run.returncode, run.stdout, run.stderr)
    assert not dest.exists()
    leftovers = [p.name for p in tmp_path.iterdir() if PARTIAL_MARK_NAME in p.name or p.name.startswith(".out")]
    assert leftovers == [], leftovers


PARTIAL_MARK_NAME = rb.PARTIAL_MARK


def test_a6_leftover_partial_folder_is_refused_and_ignored(tmp_path):
    """A6: a leftover hidden partial folder (e.g. after SIGKILL) is invalid; verify refuses it; export ignores it."""
    leftover = tmp_path / ".out.partial-deadbeef"
    leftover.mkdir()
    (leftover / "decision_tape.csv").write_text("x")
    assert rb.is_partial_output(leftover)
    with pytest.raises(rb.TapeError, match="partial output folder"):
        rb.verify(leftover)
    run = cli(["verify", leftover], "00" * 32)
    assert run.returncode == 2
    assert "partial" in run.stderr
    # a complete export beside the leftover still works; the leftover is ignored
    out, _r = _export(tmp_path, name="complete")
    rb.verify(out)
    # writing into the leftover path itself is refused
    with pytest.raises(ValueError, match="partial"):
        rb.write_output(leftover, {n: b"x" for n in rb.OUTPUT_FILES})
    # leftover is still there (export did not treat it as the dest) and dest is complete
    assert leftover.is_dir()
    assert (out / "decision_tape.csv").is_file()


def test_cli_verify_defaults_to_repo_pins(tmp_path):
    """CLI verify without flags applies repo pins, so a test-bundle folder is refused."""
    out, _r = _export(tmp_path)
    _bundle, asha = make_bundle(tmp_path)
    run = cli(["verify", out], asha)
    assert run.returncode == 2
    assert "archive_sha256" in run.stderr
    run = cli(["verify", "--self-consistency-only", out], asha)
    # still compares code hash and default policy file; those match, unofficial fixture is ok
    # but the frozen decisions + tape pin check is always on and should pass for this bundle
    assert run.returncode == 0, run.stderr


# --- Sol verify-identity follow-up (72078004 L670-713) and sidecar projection ---

def test_policy_pin_comes_from_config_not_receipt(tmp_path):
    """A receipt that stops claiming the default policy path cannot drop the policy pin."""
    out, r = _export(tmp_path)
    original = r["price_policy"]["file_sha256"]
    def mut(rec):
        rec["price_policy"]["path"] = "other-policy.json"
        rec["price_policy"]["file_sha256"] = "00" * 32
    _edit_receipt(out, mut)
    # unpinned, no expect flag: still compared to the configured policy file, not the receipt path
    with pytest.raises(rb.TapeError, match="policy"):
        rb.verify(out)
    # pinned / config identity still compares to the default policy file, not the receipt path
    with pytest.raises(rb.TapeError, match="policy file sha256"):
        rb.verify(out, pinned=True, expect_archive_sha256=r["archive_sha256"])
    with pytest.raises(rb.TapeError, match="policy file sha256"):
        rb.verify(out, expect_policy_sha256=original)


def test_pinned_verify_refuses_unresolved_policy_pin(tmp_path, monkeypatch):
    """Strict (pinned) mode refuses when the default policy file cannot be resolved."""
    out, r = _export(tmp_path)
    monkeypatch.setattr(rb, "POLICY_FILE", tmp_path / "missing-policy.json")
    with pytest.raises(rb.TapeError, match="required policy pin cannot be resolved"):
        rb.verify(out, pinned=True, expect_archive_sha256=r["archive_sha256"])
    # a CLI flag still resolves the pin
    got = rb.verify(out, pinned=True, expect_archive_sha256=r["archive_sha256"],
                    expect_policy_sha256=r["price_policy"]["file_sha256"])
    assert "policy_file_sha256" in got["_verification"]["checked"]
    assert got["_verification"]["skipped"] == ["official_sidecars"]


def test_verify_lists_checked_and_skipped_pins(tmp_path):
    """Stdout and _verification list exactly the pins that were checked and skipped."""
    out, r = _export(tmp_path)
    v = rb.verify(out)["_verification"]
    assert v["checked"] == ["csv_sha256", "code_sha256", "policy_file_sha256"]
    assert v["skipped"] == ["archive_sha256", "decisions_sha256", "official_sidecars"]
    assert v["pins"] == v["checked"]
    assert v["diagnostic_fields"] == list(rb.SIDECAR_DIAGNOSTIC_FIELDS)
    _bundle, asha = make_bundle(tmp_path)
    run = cli(["verify", "--self-consistency-only", out], asha)
    assert run.returncode == 0, run.stderr
    assert "checked: csv_sha256, code_sha256, policy_file_sha256" in run.stdout
    assert "skipped: archive_sha256, decisions_sha256, official_sidecars" in run.stdout
    assert "Diagnostic sidecar fields (not derived from decision_tape.csv):" in run.stdout
    for name in rb.SIDECAR_DIAGNOSTIC_FIELDS:
        assert name in run.stdout
    # pinned against the test-bundle archive lists every identity pin as checked
    got = rb.verify(out, pinned=True, expect_archive_sha256=r["archive_sha256"])
    assert got["_verification"]["mode"] == rb.VERIFY_WITH_PINS
    assert got["_verification"]["checked"] == list(rb.IDENTITY_PINS)
    assert got["_verification"]["skipped"] == ["official_sidecars"]


def test_sidecar_projection_mismatch_is_refused(tmp_path):
    """A labeled price_status that contradicts the plain-tape observed_price is refused."""
    out, _r = _export(tmp_path)
    path = out / "decision_tape_labeled.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    status_i = rows[0].index("price_status")
    reason_i = rows[0].index("price_reason_code")
    # the 09:30 open is the blocked PF-STATUS row (empty observed_price on the plain tape)
    rows[1][status_i] = "OK"
    rows[1][reason_i] = ""
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    rec = json.loads((out / "bridge_receipt.json").read_text())
    rec["labeled_csv_sha256"] = rb.digest(path.read_bytes())
    (out / "bridge_receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    _resign(out)
    with pytest.raises(rb.TapeError, match="labeled price_status does not match the blocked plain-tape price"):
        rb.verify(out)


def test_sidecar_diagnostic_field_must_agree_across_sidecars(tmp_path):
    """DIAGNOSTIC fields are not derived from the tape, but labeled vs log must still agree."""
    out, _r = _export(tmp_path)
    path = out / "decision_tape_labeled.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    li = rows[0].index("price_label")
    # flip a label on an accepted row (row 2 is 09:31, accepted BAR)
    rows[2][li] = "REALTIME"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    rec = json.loads((out / "bridge_receipt.json").read_text())
    rec["labeled_csv_sha256"] = rb.digest(path.read_bytes())
    (out / "bridge_receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    _resign(out)
    with pytest.raises(rb.TapeError, match="label does not match the labeled CSV"):
        rb.verify(out)


def test_verify_compares_full_tape_not_a_prefix(tmp_path):
    """A labeled CSV that is only a matching prefix of the tape (last row dropped) is refused."""
    out, r = _export(tmp_path)
    path = out / "decision_tape_labeled.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    assert len(rows) - 1 == r["rows"]
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows[:-1])  # drop the last data row
    rec = json.loads((out / "bridge_receipt.json").read_text())
    rec["labeled_csv_sha256"] = rb.digest(path.read_bytes())
    rec["rows"] = r["rows"] - 1
    rec["counts"]["ABSTAIN"] = rec["counts"]["ABSTAIN"] - 1
    (out / "bridge_receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    _resign(out)
    with pytest.raises(rb.TapeError, match="different row counts"):
        rb.verify(out)


# --- Zoodle FAIL at 72078004: official folder, edited and fully re-signed (S12, E10, S8/S9, S1/S3/S5/S6, Pb1–Pb4) ---

def _official_out(tmp_path, name="official", policy_path=None):
    """Write a complete official Lesson 001 output folder from the vendored fixture."""
    path = Path(policy_path) if policy_path is not None else ROOT / "config" / "replay_bridge_price_policy.json"
    policy, policy_sha = rb.read_policy(path)
    files, summary = rb.official_projection(policy)
    receipt = {
        **summary,
        "archive_sha256": rb.ARCHIVE_SHA,
        "csv_sha256": rb.digest(files["decision_tape.csv"]),
        "labeled_csv_sha256": rb.digest(files["decision_tape_labeled.csv"]),
        "price_block_log_sha256": rb.digest(files["price_block_log.csv"]),
        "price_accept_log_sha256": rb.digest(files["price_accept_log.csv"]),
        "price_policy": rb._receipt_policy_from_file(path) if path.resolve() == (ROOT / "config" / "replay_bridge_price_policy.json").resolve()
        else {**rb._receipt_policy_from_file(path), "path": path.name, "file_sha256": policy_sha},
        "code_sha256": rb.digest(Path(rb.__file__).read_bytes()),
        "command": "python -m optpipe.research.replay_bridge export Optpipe-Lesson-001-Portability-v3.zip output",
        "python": "3.13.5",
        "generated_at_utc": "2026-11-28T14:12:26+00:00",
    }
    # path/hash for a non-default policy file must not pretend to be the repo default
    if policy_path is not None:
        receipt["price_policy"] = {
            **rb._receipt_policy_from_file(path),
            "path": path.name,
            "file_sha256": policy_sha,
        }
    files = {**files, "bridge_receipt.json": (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()}
    out = tmp_path / name
    rb.write_output(out, files)
    return out, receipt


def _resign_official(out, rec):
    rec["csv_sha256"] = rb.digest((out / "decision_tape.csv").read_bytes())
    rec["labeled_csv_sha256"] = rb.digest((out / "decision_tape_labeled.csv").read_bytes())
    rec["price_block_log_sha256"] = rb.digest((out / "price_block_log.csv").read_bytes())
    rec["price_accept_log_sha256"] = rb.digest((out / "price_accept_log.csv").read_bytes())
    (out / "bridge_receipt.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    _resign(out)


def test_s12_approved_status_is_refused_and_notice_comes_from_config(tmp_path):
    """S12: receipt status PROPOSED → APPROVED, fully re-signed; pinned verify refuses and still prints the notice."""
    out, rec = _official_out(tmp_path)
    rec["price_policy"]["status"] = "APPROVED"
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="price_policy.status"):
        rb.verify(out, pinned=True)
    with pytest.raises(rb.TapeError, match="price_policy.status"):
        rb.verify(out)
    run = cli(["verify", out], rb.ARCHIVE_SHA)
    assert run.returncode == 2, run.stderr
    assert "price_policy.status" in run.stderr
    run = cli(["verify", "--self-consistency-only", out], rb.ARCHIVE_SHA)
    assert run.returncode == 2, run.stderr
    assert "price_policy.status" in run.stderr
    # a clean official folder still prints the notice from the configured policy
    out2, _r = _official_out(tmp_path, name="clean")
    run = cli(["verify", out2], rb.ARCHIVE_SHA)
    assert run.returncode == 0, run.stderr
    assert "Policy status PROPOSED is not owner-approved" in run.stdout
    assert "official_sidecars" in run.stdout
    assert "skipped: none" in run.stdout


def test_e10_s4_sd2_bar_relabeled_delayed_is_refused(tmp_path):
    """E10 / S4 / SD2: a BAR row relabelled DELAYED in the labeled CSV, accept log and receipt, then re-signed."""
    out, rec = _official_out(tmp_path)
    labeled = out / "decision_tape_labeled.csv"
    rows = list(csv.reader(labeled.read_text().splitlines()))
    li = rows[0].index("price_label")
    # row 2 is 09:31, an accepted BAR bar_close_1m
    assert rows[2][li] == "BAR"
    rows[2][li] = "DELAYED"
    with labeled.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    acc = out / "price_accept_log.csv"
    arows = list(csv.reader(acc.read_text().splitlines()))
    ali = arows[0].index("label")
    # accept log row 1 is tape row 1 (09:31)
    arows[1][ali] = "DELAYED"
    with acc.open("w", newline="") as fh:
        csv.writer(fh).writerows(arows)
    rec["price_accepts"]["by_label"] = {"BAR": rec["price_accepts"]["by_label"]["BAR"] - 1, "DELAYED": 1}
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="does not match the projection from the pinned archive"):
        rb.verify(out, pinned=True)


def test_s9_block_reason_changed_in_labeled_csv_is_refused(tmp_path):
    """S9: block reason PF-STATUS → PF-STALE in the labeled CSV only, fully re-signed."""
    out, rec = _official_out(tmp_path)
    path = out / "decision_tape_labeled.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    ri = rows[0].index("price_reason_code")
    assert rows[1][ri] == "PF-STATUS"
    rows[1][ri] = "PF-STALE"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="reason does not match the labeled CSV|does not match the projection"):
        rb.verify(out, pinned=True)


def test_s8_block_reason_changed_in_log_and_receipt_is_refused(tmp_path):
    """S8: block reason PF-STATUS → PF-STALE in the block log, labeled CSV and receipt, fully re-signed."""
    out, rec = _official_out(tmp_path)
    for name, col in (("decision_tape_labeled.csv", "price_reason_code"), ("price_block_log.csv", "reason_code")):
        path = out / name
        rows = list(csv.reader(path.read_text().splitlines()))
        i = rows[0].index(col)
        rows[1][i] = "PF-STALE"
        with path.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)
    rec["price_blocks"]["by_reason"] = {"PF-STALE": 1}
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="does not match the projection from the pinned archive"):
        rb.verify(out, pinned=True)


def test_s1_sd1_labeled_age_cell_edited_is_refused(tmp_path):
    """S1 / SD1: labeled price_age_seconds edited and fully re-signed."""
    out, rec = _official_out(tmp_path)
    path = out / "decision_tape_labeled.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    i = rows[0].index("price_age_seconds")
    rows[2][i] = "99"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="age does not match the labeled CSV|does not match the projection"):
        rb.verify(out, pinned=True)


def test_s3_price_kind_edited_is_refused(tmp_path):
    """S3: labeled price_kind edited and fully re-signed."""
    out, rec = _official_out(tmp_path)
    path = out / "decision_tape_labeled.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    i = rows[0].index("price_kind")
    rows[2][i] = "quote_last"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="price_kind does not match the labeled CSV|does not match the projection"):
        rb.verify(out, pinned=True)


def test_s5_sd4_as_of_timestamp_edited_is_refused(tmp_path):
    """S5 / SD4: block-log as_of_raw edited and fully re-signed."""
    out, rec = _official_out(tmp_path)
    path = out / "price_block_log.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    i = rows[0].index("as_of_raw")
    rows[1][i] = "1999-01-01T00:00:00-05:00"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="does not match the projection from the pinned archive"):
        rb.verify(out, pinned=True)


def test_s6_sd7_accept_log_age_edited_is_refused(tmp_path):
    """S6 / SD7: accept-log age_seconds edited and fully re-signed."""
    out, rec = _official_out(tmp_path)
    path = out / "price_accept_log.csv"
    rows = list(csv.reader(path.read_text().splitlines()))
    i = rows[0].index("age_seconds")
    rows[1][i] = "99"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="age does not match the labeled CSV|does not match the projection"):
        rb.verify(out, pinned=True)


def test_pb1_alternate_policy_identity_is_refused_in_pinned_mode(tmp_path):
    """Pb1–Pb3: receipt policy path/status/sha/numbers changed to an alternate identity, fully re-signed."""
    out, rec = _official_out(tmp_path)
    rec["price_policy"]["path"] = "config/alt_policy.json"
    rec["price_policy"]["status"] = "ADOPTED"
    rec["price_policy"]["file_sha256"] = "ab" * 32
    rec["price_policy"]["max_age_seconds"]["quote_last"] = "99999"
    _resign_official(out, rec)
    with pytest.raises(rb.TapeError, match="policy"):
        rb.verify(out, pinned=True)
    with pytest.raises(rb.TapeError, match="policy"):
        rb.verify(out)
    run = cli(["verify", out], rb.ARCHIVE_SHA)
    assert run.returncode == 2, run.stderr
    assert "pinned" not in run.stdout  # must not print pinned-mode success
    assert "policy" in run.stderr
    run = cli(["verify", "--self-consistency-only", out], rb.ARCHIVE_SHA)
    assert run.returncode == 2, run.stderr
    assert "policy" in run.stderr


def test_pb4_official_export_with_alternate_policy_is_refused(tmp_path):
    """Pb4: a genuine official-archive export made with an alternate loose ADOPTED policy is refused when pinned."""
    alt = tmp_path / "alt.json"
    raw = json.loads((ROOT / "config" / "replay_bridge_price_policy.json").read_text())
    raw["status"] = "ADOPTED"
    raw["bar_close_1m"]["max_age_seconds"] = "99999"
    alt.write_text(json.dumps(raw))
    out, rec = _official_out(tmp_path, policy_path=alt)
    assert rec["price_policy"]["status"] == "ADOPTED"
    assert rec["price_policy"]["path"] == "alt.json"
    with pytest.raises(rb.TapeError, match="policy|does not match the projection"):
        rb.verify(out, pinned=True)
    run = cli(["verify", out], rb.ARCHIVE_SHA)
    assert run.returncode == 2
    assert "pinned" not in run.stdout


# --- Zoodle re-gate at 7ec5a19f: self-only policy compare (R14, S12, Pb1-Pb3, Pc1-Pc8) ---

def _self_only_policy_refused(out, match="policy"):
    with pytest.raises(rb.TapeError, match=match):
        rb.verify(out)
    run = cli(["verify", "--self-consistency-only", out], rb.ARCHIVE_SHA)
    assert run.returncode == 2, run.stderr
    assert "policy" in run.stderr


def test_pb2_alt_json_path_is_refused_in_self_only(tmp_path):
    """Pb2: receipt policy path changed to alt.json, fully re-signed; refused in self-only."""
    out, rec = _official_out(tmp_path)
    rec["price_policy"]["path"] = "alt.json"
    _resign_official(out, rec)
    _self_only_policy_refused(out)


def test_pb3_dotted_config_path_is_refused_in_self_only(tmp_path):
    """Pb3: receipt policy path spelled ./config/... with loosened numbers, fully re-signed."""
    out, rec = _official_out(tmp_path)
    rec["price_policy"]["path"] = "./config/replay_bridge_price_policy.json"
    rec["price_policy"]["max_age_seconds"]["quote_last"] = "99999"
    _resign_official(out, rec)
    _self_only_policy_refused(out)


def _pc_mut(pid):
    """Zoodle harness7 Pc1–Pc8 receipt mutations (official folder, then fully re-signed)."""
    def pc1(rec):
        del rec["price_policy"]["path"]
    def pc2(rec):
        rec["price_policy"].pop("path", None)
        rec["price_policy"]["file_sha256"] = "ab" * 32
        rec["price_policy"]["max_age_seconds"]["quote_last"] = "99999"
    def pc3(rec):
        rec.pop("price_policy")
    def pc4(rec):
        rec["price_policy"] = {}
    def pc5(rec):
        rec["price_policy"].pop("file_sha256")
    def pc6(rec):
        rec["price_policy"]["path"] = ""
        rec["price_policy"]["file_sha256"] = "ab" * 32
        rec["price_policy"]["max_age_seconds"]["quote_last"] = "99999"
    def pc7(rec):
        rec["price_policy"].pop("status_note")
    def pc8(rec):
        rec["price_policy"]["delayed_quote"] = {
            "absolute_ceiling_seconds": "99999",
            "adjusted_max_seconds": "99999",
            "max_declared_delay_seconds": "900",
        }
    return {"Pc1": pc1, "Pc2": pc2, "Pc3": pc3, "Pc4": pc4,
            "Pc5": pc5, "Pc6": pc6, "Pc7": pc7, "Pc8": pc8}[pid]


@pytest.mark.parametrize("pid", ["Pc1", "Pc2", "Pc3", "Pc4", "Pc5", "Pc6", "Pc7", "Pc8"])
def test_pc_receipt_policy_edits_are_refused_in_self_only(tmp_path, pid):
    """Pc1–Pc8: official folder, receipt policy edited and fully re-signed; refused in self-only."""
    out, rec = _official_out(tmp_path)
    _pc_mut(pid)(rec)
    _resign_official(out, rec)
    _self_only_policy_refused(out, match="policy")
