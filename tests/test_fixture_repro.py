"""CC-001: scripts/check_fixture_repro.py (fixture-workflow reproducibility and integrity check). Paper only.

In-process: one real cell output (the CLI's run + replay watch) is the positive control, and copies of it are
tampered to show each check catches what it claims. A wall-clock-only change, with every hash re-chained, must
NOT be reported as a difference; a content change re-chained the same way must be (integrity alone cannot see it).
End to end: the script itself on two cells, then a third cell in a fresh process checked against that fingerprint.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_fixture_repro.py"
FIXTURES = ROOT / "tests" / "fixtures" / "2026-10-13"
STEM = "session-2026-10-13"


def _load():
    spec = importlib.util.spec_from_file_location("check_fixture_repro", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cfr = _load()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="module")
def cell_out(tmp_path_factory):
    """One cell's outputs, produced by the CLI exactly as the script's cells produce them (own out dir)."""
    from optpipe.cli import main
    out = tmp_path_factory.mktemp("cc001") / "out"
    assert main(["run", "--fixtures", str(FIXTURES), "--out", str(out)]) == 0
    assert main(["validate", str(out / f"{STEM}.json")]) == 0
    assert main(["watch", "--session", str(out / f"{STEM}.json"), "--replay", str(FIXTURES / "watch_tape.yaml"),
                 "--out", str(out)]) == 0
    return out


@pytest.fixture
def out(cell_out, tmp_path):
    dst = tmp_path / "out"
    shutil.copytree(cell_out, dst)
    return dst


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def rechain(out: Path, edit_record=None, edit_trace=None) -> None:
    """Edit the record and/or trace, then recompute every hash in the chain the way write_session/write_watch do:
    session_ref -> trace bytes -> trace sidecar -> trace_ref -> record bytes -> watch.session -> watch sidecar."""
    from optpipe.records.store import session_content_sha256
    rp, tp, wp = out / f"{STEM}.json", out / f"{STEM}.trace.json", out / f"{STEM}.watch.json"
    rec, tr, w = _read(rp), _read(tp), _read(wp)
    if edit_record:
        edit_record(rec)
    if edit_trace:
        edit_trace(tr)
    tr["session_ref"]["sha256"] = session_content_sha256(rec)
    tb = (json.dumps(tr, indent=2) + "\n").encode("utf-8")
    tp.write_bytes(tb)
    (out / f"{tp.name}.sha256").write_bytes(f"{_sha(tb)}  {tp.name}\n".encode())
    rec["trace_ref"]["sha256"] = _sha(tb)
    rb = (json.dumps(rec, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    rp.write_bytes(rb)
    w["session"]["sha256"] = _sha(rb)
    wb = (json.dumps(w, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    wp.write_bytes(wb)
    (out / f"{wp.name}.sha256").write_bytes(f"{_sha(wb)}  {wp.name}\n".encode())


def _failed(res) -> set[str]:
    return {c for c, ok in res["checks"].items() if not ok}


# ------------------------------------------------------------------ positive control
def test_real_cell_passes_every_check(cell_out):
    res = cfr.check_outputs(cell_out)
    assert _failed(res) == set() and res["errors"] == []
    steps = sum(len(r["steps"]) for r in _read(cell_out / f"{STEM}.trace.json")["runs"])
    assert steps >= 5
    # exactly the declared volatile values are masked: two per trace step, one Markdown line per step
    assert res["masked"] == {"record": 1, "trace": 2 * steps, "markdown": steps, "watch": 1}
    assert set(res["digests"]) == {"record", "trace", "markdown", "watch"}


# ------------------------------------------------------------------ reproducibility (R1)
def test_wall_clock_only_change_is_not_a_difference(cell_out, out):
    before = cfr.check_outputs(cell_out)["digests"]
    raw_before = (cell_out / f"{STEM}.json").read_bytes()

    def retime(tr):
        for run in tr["runs"]:
            for s in run["steps"]:
                s["started_at"], s["duration_ms"] = "2030-01-02T03:04:05.678901Z", s["duration_ms"] + 987.65
    rechain(out, edit_trace=retime)
    md = out / f"{STEM}.md"
    md.write_bytes(cfr.MD_TIMING.sub(r"\1 4321 ms", md.read_text(encoding="utf-8")).encode("utf-8"))

    res = cfr.check_outputs(out)
    assert (out / f"{STEM}.json").read_bytes() != raw_before          # the raw record really changed
    assert _failed(res) == set(), res["errors"]                        # and every hash still checks out
    assert res["digests"] == before                                    # but nothing reproducible changed


def test_content_change_with_consistent_hashes_is_caught(cell_out, out):
    """A record that changed content but re-chained every hash passes C2-C6; only R1 can see it."""
    before = cfr.check_outputs(cell_out)["digests"]

    def requantity(rec):
        rec["tickets"][0]["quantity"] += 1
    rechain(out, edit_record=requantity)
    res = cfr.check_outputs(out)
    assert _failed(res) == set(), res["errors"]
    assert res["digests"]["record"] != before["record"]
    assert cfr.fingerprint(res["digests"]) != cfr.fingerprint(before)


def test_trace_content_change_is_caught(cell_out, out):
    before = cfr.check_outputs(cell_out)["digests"]

    def rename(tr):
        tr["runs"][0]["steps"][0]["stage"] = "discovery-x"
    rechain(out, edit_trace=rename)
    res = cfr.check_outputs(out)
    assert _failed(res) == set() and res["digests"]["trace"] != before["trace"]


# ------------------------------------------------------------------ integrity (C2-C7), fail closed
def test_tampered_trace_fails_sidecar_and_trace_ref(out):
    tp = out / f"{STEM}.trace.json"
    tr = _read(tp)
    tr["runs"][0]["steps"][0]["duration_ms"] += 1
    tp.write_bytes((json.dumps(tr, indent=2) + "\n").encode("utf-8"))
    assert _failed(cfr.check_outputs(out)) == {"C2", "C3"}


def test_tampered_record_fails_session_ref_and_watch_session(out):
    rp = out / f"{STEM}.json"
    rec = _read(rp)
    rec["tickets"][0]["quantity"] += 1
    rp.write_bytes((json.dumps(rec, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    assert _failed(cfr.check_outputs(out)) == {"C4", "C6"}


@pytest.mark.parametrize("residecar", [False, True])
def test_tampered_watch_fails_c5(out, residecar):
    """Without a fresh sidecar the hash catches it; with one, validate_watch still does (no 'alert only' text)."""
    wp = out / f"{STEM}.watch.json"
    w = _read(wp)
    w["alerts"][0]["message"] = "edited"
    wb = (json.dumps(w, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    wp.write_bytes(wb)
    if residecar:
        (out / f"{wp.name}.sha256").write_bytes(f"{_sha(wb)}  {wp.name}\n".encode())
    res = cfr.check_outputs(out)
    assert _failed(res) == {"C5"}
    assert f"sidecar ok={residecar}" in res["errors"][0] and "alert only, no order" in res["errors"][0]


@pytest.mark.parametrize("variant,why", [("crlf", "CR line endings"), ("cp1252", "is not UTF-8"),
                                         ("no_final_newline", "no final newline")])
def test_windows_text_mode_bytes_fail_c7(out, variant, why):
    """Simulated (not a Windows run): bytes that Path.write_text() without encoding/newline can give on a cp1252
    Windows locale, and a truncated last line. The Markdown is not covered by any hash: only C7 sees it."""
    md = out / f"{STEM}.md"
    text = md.read_text(encoding="utf-8")
    assert "·" in text                                           # the title's middle dot
    data = {"crlf": lambda: text.replace("\n", "\r\n").encode("utf-8"), "cp1252": lambda: text.encode("cp1252"),
            "no_final_newline": lambda: text.rstrip("\n").encode("utf-8")}[variant]()
    md.write_bytes(data)
    res = cfr.check_outputs(out)
    assert _failed(res) == {"C7"} and why in " ".join(res["errors"])


def test_missing_output_fails_closed(out):
    (out / f"{STEM}.watch.json").unlink()
    res = cfr.check_outputs(out)
    assert _failed(res) == {"C2", "C3", "C4", "C5", "C6", "C7"} and "unreadable" in res["errors"][0]
    assert res["digests"] == {}


def test_mask_touches_only_declared_paths():
    doc = {"runs": [{"started_at": "keep", "steps": [{"started_at": "a", "duration_ms": 1, "stage": "s"}]}],
           "started_at": "keep"}
    n = sum(cfr.mask(doc, p) for p in cfr.VOLATILE["trace"])
    assert n == 2
    assert doc == {"runs": [{"started_at": "keep", "steps": [{"started_at": cfr.MASK, "duration_ms": cfr.MASK,
                                                              "stage": "s"}]}], "started_at": "keep"}


# ------------------------------------------------------------------ C0 and the verdict over cells
@pytest.mark.parametrize("got,tz,seed,ok", [
    ({"utc_offset_s": 7200, "hash_randomization": 1}, "Europe/Berlin", "1", True),
    ({"utc_offset_s": 0, "hash_randomization": 1}, "Europe/Berlin", "1", False),      # TZ ignored (e.g. Windows)
    ({"utc_offset_s": 7200, "hash_randomization": 1}, "Europe/Berlin", "0", False),   # seed 0 not applied
    ({"utc_offset_s": 3600, "hash_randomization": 0}, None, "0", True),               # host zone: offset unchecked
    (None, "UTC", "0", False),                                                        # probe failed
])
def test_env_applied(got, tz, seed, ok):
    assert cfr.env_applied(got, tz, seed)[0] is ok


def _cell(fp="a" * 64, ok=True, record="r"):
    return {"TZ": "UTC", "PYTHONHASHSEED": "0", "fingerprint": fp, "errors": [] if ok else ["C4: x"],
            "checks": {c: (ok or c != "C4") for c in cfr.CHECKS},
            "normalized_sha256": {"record": record, "trace": "t", "markdown": "m", "watch": "w"}}


def test_summarize_verdicts():
    assert cfr.summarize([_cell(), _cell()], None)["verdict"] == "PASS"
    assert cfr.summarize([_cell(), _cell()], "a" * 64)["expect_match"] is True
    s = cfr.summarize([_cell(), _cell(fp="b" * 64, record="r2")], None)              # cells disagree
    assert s["verdict"] == "FAIL" and not s["reproducible"] and s["fingerprint"] is None
    assert s["failures"] == ["R1: record differs between cells after masking the volatile fields"]
    s = cfr.summarize([_cell(), _cell(ok=False)], None)                               # one check failed
    assert s["verdict"] == "FAIL" and s["reproducible"] and not s["integrity_ok"] and "C4: x" in s["failures"][0]
    assert cfr.summarize([_cell()], "b" * 64)["verdict"] == "FAIL"                    # --expect mismatch
    assert cfr.summarize([], None)["verdict"] == "FAIL"                               # no cells is no evidence


# ------------------------------------------------------------------ the script, end to end
def _script(*args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=900, env=env, check=False)


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="the TZ axis needs time.tzset (POSIX)")
def test_script_end_to_end_two_cells_then_a_third_against_the_fingerprint(tmp_path):
    r1 = tmp_path / "r1.json"
    p = _script("--cell", "UTC,0", "--cell", "America/New_York,1", "--expect", "0" * 64, "--receipt", str(r1))
    assert p.returncode == 1, p.stdout + p.stderr                      # wrong --expect: FAIL, everything else ok
    rec = json.loads(r1.read_text(encoding="utf-8"))
    assert rec["integrity_ok"] and rec["reproducible"] and rec["expect_match"] is False
    assert rec["verdict"] == "FAIL" and len(rec["fingerprint"]) == 64
    assert [c["probe"]["utc_offset_s"] for c in rec["cells"]] == [0, -14400]
    assert [c["probe"]["hash_randomization"] for c in rec["cells"]] == [0, 1]
    assert all(all(c["checks"].values()) for c in rec["cells"])
    assert rec["spec"]["version"] == (ROOT / "spec" / "VERSION").read_text(encoding="utf-8").strip()
    text = r1.read_text(encoding="utf-8")
    assert str(ROOT) not in text and str(tmp_path) not in text and "\r" not in text   # shareable receipt

    r2 = tmp_path / "r2.json"
    p = _script("--cell", "Europe/Berlin,7", "--expect", rec["fingerprint"], "--receipt", str(r2))
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(r2.read_text(encoding="utf-8"))["verdict"] == "PASS" and "verdict: PASS" in p.stdout


@pytest.mark.parametrize("args,why", [
    (("--cell", "Not/AZone,0"), "time zone 'Not/AZone' is not available"),
    (("--cell", "UTC"), "expected TZ,SEED"),
    (("--cell", "UTC,4294967296"), "SEED 0..4294967295"),
    (("--expect", "abc"), "64-character"),
])
def test_bad_arguments_are_exit_2(args, why):
    p = _script(*args)
    assert p.returncode == 2 and why in p.stderr, p.stderr


def test_unpinned_spec_is_exit_2_and_runs_nothing(tmp_path):
    import os
    spec = tmp_path / "spec"
    shutil.copytree(ROOT / "spec", spec, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    rules = spec / "risk_rules.yaml"
    rules.write_bytes(rules.read_bytes() + b"# edited\n")
    p = _script("--cell", "UTC,0", env=dict(os.environ, OPTIONS_SPEC_DIR=str(spec)))
    assert p.returncode == 2 and "SPEC-PIN-MISMATCH" in p.stderr, p.stderr
    assert "verdict" not in p.stdout
