"""Extends test_r7_7 from pack selection to discovery, research and the watcher.

Same matrix as test_r7_7_select_pack_replay_is_deterministic_and_fail_closed: PYTHONHASHSEED
{0, 1} x UTC, Europe/Berlin, America/New_York, fixed injected clocks, inherited OPTPIPE_NETGUARD.
Hostile packs are private labeled temp copies. spec/ and the pinned EXAMPLE pack are not edited.

closure_claim: none. A green result is evidence for the replay this file runs. It does not close
R7, MD-18, R6, or paper.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from mdpack import ROOT, SPEC
from optpipe.cli import main

BASE_HEAD = "7ee665444eb4d3197effb95347af9bfbf9b7d787"
BASE_TREE = "35afda7460126e07f5a127a2a0043afeb315ed30"
RECEIPT = ROOT / "tests" / "receipts" / "r7_item7_deterministic_replay.json"
DRIVER = ROOT / "tests" / "r7_item7_driver.py"
FIXTURES = ROOT / "tests" / "fixtures" / "2026-10-13"
SEEDS = ("0", "1")
TZS = ("UTC", "Europe/Berlin", "America/New_York")
SLICE_FILES = {
    "tests/r7_item7_driver.py",
    "tests/test_r7_7_failure_replay.py",
    "tests/test_pr_b_sol_r7.py",
    "tests/receipts/r7_item7_deterministic_replay.json",
}
SAME_CLOCK = ("positive", "missing", "malformed", "tamper", "oversized", "wrong_path", "lookahead")
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
NEWLINE_SHA = hashlib.sha256(b"\n").hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _slice_paths() -> set[str]:
    changed = set(_git("diff", "--name-only", BASE_HEAD).split())
    changed |= set(_git("diff", "--name-only", "--cached", BASE_HEAD).split())
    changed |= set(_git("diff", "--name-only", BASE_HEAD, "HEAD").split())
    others = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True)
    changed |= {n for n in others.split() if n}
    return {n for n in changed if n}


def _satisfies(actual: dict, expected: dict) -> None:
    for key, want in expected.items():
        if key.endswith("_include"):
            field = key[: -len("_include")]
            assert set(want) <= set(actual[field]), (key, want, actual[field])
        else:
            assert actual[key] == want, (key, want, actual.get(key))


def _session(tmp_path: Path) -> Path:
    out = tmp_path / "session-src"
    rc = main(["run", "--fixtures", str(FIXTURES), "--out", str(out)])
    assert rc == 0
    session = out / "session-2026-10-13.json"
    trace = out / "session-2026-10-13.trace.json"
    assert session.is_file() and trace.is_file()
    return session


def _run_cell(work: Path, session: Path, seed: str, tz: str) -> None:
    dest_in = work / "in"
    dest_in.mkdir(parents=True)
    shutil.copy2(session, dest_in / session.name)
    shutil.copy2(session.with_name("session-2026-10-13.trace.json"), dest_in / "session-2026-10-13.trace.json")
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = seed
    env["TZ"] = tz
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    assert env.get("OPTPIPE_NETGUARD") == "1"
    argv = [sys.executable, str(DRIVER), "--work", str(work / "out"), "--session", str(dest_in / session.name)]
    proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    cell = json.loads((work / "out" / "cell.json").read_text())
    assert cell["closure_claim"] == "none"
    assert cell["pythonhashseed"] == seed
    assert cell["tz"] == tz
    assert cell["optpipe_netguard"] == "1"
    assert cell["pythondontwritebytecode"] == "1"
    assert cell["argv0_name"] == "r7_item7_driver.py"
    assert cell["argv_rest"] == ["--work", str(work / "out"), "--session", str(dest_in / session.name)]
    assert cell["python"]


def test_r7_7_failure_replay_receipt(tmp_path):
    """Byte-identical raw projections and, separately, id_status_v1 semantic files."""
    assert _git("rev-parse", f"{BASE_HEAD}^{{tree}}") == BASE_TREE
    subprocess.check_call(["git", "merge-base", "--is-ancestor", BASE_HEAD, "HEAD"], cwd=ROOT)
    extra = _slice_paths() - SLICE_FILES
    assert extra == set(), extra
    assert not any(n.startswith(("src/", "spec/", "config/", "scripts/")) for n in _slice_paths())

    receipt = json.loads(RECEIPT.read_text())
    assert receipt["closure_claim"] == "none"
    blob = RECEIPT.read_text()
    assert "closes R7" not in blob and "R7 is closed" not in blob
    assert "MD-18 closed" not in blob and "R6 closed" not in blob
    capture = receipt["author_environment_capture"]
    assert capture["closure_claim"] == "none"
    assert capture["python"] and capture["dependencies"]
    for dist, ver in capture["dependencies"].items():
        assert ver
        assert importlib.metadata.version(dist)  # live version is recorded by being resolvable, not frozen
    assert Path(os.environ["OPTIONS_SPEC_DIR"]).resolve() == SPEC.resolve()

    for name, rel in (
        ("settings_sha256", "config/settings.yaml"),
        ("risk_rules_sha256", "spec/risk_rules.yaml"),
        ("watcher_rules_sha256", "spec/watcher_rules.yaml"),
        ("pack_pin_sha256", receipt["inputs"]["pack_pin"]),
    ):
        digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert digest == receipt["inputs"][name]

    session = _session(tmp_path)
    cells = []
    for seed in SEEDS:
        for tz in TZS:
            work = tmp_path / f"cell-{seed}-{tz.replace('/', '-')}"
            _run_cell(work, session, seed, tz)
            cells.append(work / "out")
    assert len({c.resolve() for c in cells}) == 6

    first = cells[0]
    for case, body in receipt["cases"].items():
        raw_paths = [c / "raw" / f"{case}.json" for c in cells]
        sem_paths = [c / "semantic" / f"{case}.json" for c in cells]
        raw_blobs = [p.read_bytes() for p in raw_paths]
        sem_blobs = [p.read_bytes() for p in sem_paths]
        assert len(set(raw_blobs)) == 1, case
        assert len(set(sem_blobs)) == 1, case
        assert raw_blobs[0] != sem_blobs[0], case
        assert len(raw_blobs[0]) > 80, case
        raw_sha = hashlib.sha256(raw_blobs[0]).hexdigest()
        sem_sha = hashlib.sha256(sem_blobs[0]).hexdigest()
        assert raw_sha not in (EMPTY_SHA, NEWLINE_SHA)
        assert sem_sha not in (EMPTY_SHA, NEWLINE_SHA)
        assert raw_sha == body["raw_sha256"]
        assert sem_sha == body["semantic_sha256"]
        assert raw_sha != sem_sha
        actual = json.loads(sem_blobs[0])
        assert actual == body["actual"]
        assert actual["closure_claim"] == "none"
        assert json.loads(raw_blobs[0])["closure_claim"] == "none"
        _satisfies(actual, body["expected"])
        # semantic file is the named normalization; raw file is not that normalization
        assert b'"normalization"' not in raw_blobs[0]
        assert b'"id_status_v1"' in sem_blobs[0]

    positive = json.loads((first / "semantic" / "positive.json").read_text())
    assert positive["research_quote_status"] == "ok"
    assert positive["research_quote_value"] == 101.25
    assert positive["discovery_jpm_last"] == 101.25
    assert positive["pack_ok"] is True and positive["error_ids"] == []
    pos_raw = hashlib.sha256((first / "raw" / "positive.json").read_bytes()).hexdigest()
    for case in receipt["cases"]:
        if case == "positive":
            continue
        other = json.loads((first / "semantic" / f"{case}.json").read_text())
        assert other["research_quote_value"] != 101.25
        assert hashlib.sha256((first / "raw" / f"{case}.json").read_bytes()).hexdigest() != pos_raw

    watch = {}
    for case in SAME_CLOCK:
        digests = set()
        for cell in cells:
            files = sorted((cell / "watch" / case).glob("*.watch.json"))
            assert len(files) == 1
            digests.add(hashlib.sha256(files[0].read_bytes()).hexdigest())
        assert len(digests) == 1, case
        watch[case] = digests.pop()
    assert len(set(watch.values())) == 1  # same clock, vetoed session: watch.json does not mark the control
    assert not list((first / "watch" / "naive_clock").glob("*.watch.json"))
    for case in ("future", "stale", "calendar"):
        digests = {hashlib.sha256(p.read_bytes()).hexdigest()
                   for cell in cells for p in (cell / "watch" / case).glob("*.watch.json")}
        assert len(digests) == 1
        assert digests.pop() != watch["positive"]
