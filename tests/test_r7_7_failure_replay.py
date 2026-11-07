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

import pytest

from mdpack import ROOT, SPEC
from optpipe.cli import main

BASE_HEAD = "ae95c68cc10240fe75b41cc30272c463d960f5e3"
BASE_TREE = "c4af5232c75bbb566482285469772f35dc872eae"
# sha256 of sorted '<mode> <blob> <path>' lines from git ls-files -s, excluding SLICE_FILES.
# Retargeted to origin/main ae95c68. Case raw/semantic hashes are not part of this pin.
OUTSIDE_SLICE_SHA256 = "c18425270b5c2c4673ff3601d4e67cba8c239670dd4f4e274d977c8b7fcbabe7"
INPUT_PINS = (
    ("settings_sha256", "config/settings.yaml",
     "f89369fe485d01cc42a548ae4a493b4bff53d181d5862f45e4c4693f8c7fd053"),
    ("risk_rules_sha256", "spec/risk_rules.yaml",
     "6badc513716649de953b8689285f9b67a63035202541bcb575467f7999fa3b11"),
    ("watcher_rules_sha256", "spec/watcher_rules.yaml",
     "21e294555b6795d6cacfea3c02ce8ea918a2bebd68250ed326a2b6f45a75ba10"),
    ("pack_pin_sha256", "spec/fixtures/market_data/valid/2026-10-20/pack.sha256",
     "a724aaf3347fcb46544baddfdefe16bf3f90b421f16842adb77712c1c5582365"),
)
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
_FORBIDDEN_PREFIXES = ("src/", "spec/", "config/", "scripts/")


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()}")
    return proc


def _commit_exists(root: Path, sha: str) -> bool:
    """Local object check only. A missing object is exit 128 from git; that is absence, not a fetch."""
    proc = _git(root, "cat-file", "-e", f"{sha}^{{commit}}", check=False)
    return proc.returncode == 0


def _outside_slice_sha256(root: Path, slice_files: set[str]) -> str:
    raw = subprocess.check_output(["git", "ls-files", "-s", "-z"], cwd=root)
    lines = []
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        meta, path_b = rec.split(b"\t", 1)
        mode, blob, _stage = meta.decode().split(" ")
        path = path_b.decode()
        if path in slice_files:
            continue
        lines.append(f"{mode} {blob} {path}")
    payload = ("\n".join(sorted(lines)) + "\n").encode()
    return hashlib.sha256(payload).hexdigest()


def _changed_against_head(root: Path) -> set[str]:
    """Worktree and index versus HEAD. HEAD exists on a depth-1 checkout; the base commit may not."""
    names: set[str] = set()
    for args in (["diff", "--name-only", "HEAD"], ["diff", "--name-only", "--cached"]):
        names.update(n for n in _git(root, *args).stdout.splitlines() if n)
    others = _git(root, "ls-files", "--others", "--exclude-standard").stdout
    names.update(n for n in others.splitlines() if n)
    return names


def verify_receipt_pins(root, receipt, base_head, base_tree, input_pins, outside_sha256):
    assert receipt["closure_claim"] == "none"
    assert receipt["source"]["base_head"] == base_head
    assert receipt["source"]["base_tree"] == base_tree
    assert receipt["identity"]["closure_claim"] == "none"
    assert receipt["identity"]["base_head"] == base_head
    assert receipt["identity"]["base_tree"] == base_tree
    assert receipt["identity"]["outside_slice_sha256"] == outside_sha256
    for field, rel, digest in input_pins:
        assert receipt["inputs"][field] == digest
        got = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        assert got == digest, rel


def verify_base_identity(root: Path, receipt: dict, *, base_head: str, base_tree: str,
                         slice_files: set[str], input_pins: tuple, outside_sha256: str) -> None:
    """Require the pinned base identity. Never fetches. Does not return before the pin checks.

    When the base commit object is present, also require its tree and ancestry.
    When it is absent, the manifest, input pins, and HEAD diff are the whole check.
    """
    verify_receipt_pins(root, receipt, base_head, base_tree, input_pins, outside_sha256)
    assert _outside_slice_sha256(root, slice_files) == outside_sha256
    dirty = _changed_against_head(root)
    assert dirty <= set(slice_files), dirty
    assert not any(p.startswith(_FORBIDDEN_PREFIXES) for p in dirty)
    if _commit_exists(root, base_head):
        got = _git(root, "rev-parse", f"{base_head}^{{tree}}").stdout.strip()
        assert got == base_tree
        ancestry = _git(root, "merge-base", "--is-ancestor", base_head, "HEAD", check=False)
        assert ancestry.returncode == 0, ancestry.stderr.strip()
        diff = {n for n in _git(root, "diff", "--name-only", base_head, "HEAD").stdout.splitlines() if n}
        assert diff <= set(slice_files), diff
        assert not any(p.startswith(_FORBIDDEN_PREFIXES) for p in diff)


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


def assert_receipt_contract(receipt: dict) -> None:
    """Receipt checks that do not need the base git object. The matrix runs after this."""
    assert receipt["closure_claim"] == "none"
    blob = json.dumps(receipt)
    assert "closes R7" not in blob and "R7 is closed" not in blob
    assert "MD-18 closed" not in blob and "R6 closed" not in blob
    capture = receipt["author_environment_capture"]
    assert capture["closure_claim"] == "none"
    assert capture["python"] and capture["dependencies"]
    for dist, ver in capture["dependencies"].items():
        assert ver
        assert importlib.metadata.version(dist)  # live version is recorded by being resolvable, not frozen
    positive = receipt["cases"]["positive"]["actual"]
    assert positive["research_quote_status"] == "ok"
    assert positive["research_quote_value"] == 101.25
    assert positive["closure_claim"] == "none"
    for name, body in receipt["cases"].items():
        assert body["actual"]["closure_claim"] == "none"
        assert body["raw_sha256"] not in (EMPTY_SHA, NEWLINE_SHA)
        assert body["semantic_sha256"] not in (EMPTY_SHA, NEWLINE_SHA)
        assert body["raw_sha256"] != body["semantic_sha256"], name
        if name != "positive":
            assert body["actual"]["research_quote_value"] != 101.25


def test_r7_7_failure_replay_receipt(tmp_path):
    """Byte-identical raw projections and, separately, id_status_v1 semantic files."""
    receipt = json.loads(RECEIPT.read_text())
    verify_receipt_pins(ROOT, receipt, BASE_HEAD, BASE_TREE, INPUT_PINS, OUTSIDE_SLICE_SHA256)
    assert_receipt_contract(receipt)
    assert Path(os.environ["OPTIONS_SPEC_DIR"]).resolve() == SPEC.resolve()

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


def _git_commit(repo: Path, message: str) -> None:
    subprocess.check_call([
        "git", "-c", "user.email=poodle@example.com", "-c", "user.name=Poodle",
        "-c", "commit.gpgsign=false", "commit", "-m", message,
    ], cwd=repo)


def _shallow_fixture(tmp_path: Path):
    """Depth-1 clone: the tip is present and its parent, and BASE_HEAD, are not."""
    origin = tmp_path / "origin"
    origin.mkdir(parents=True)
    subprocess.check_call(["git", "init", "-q"], cwd=origin)
    (origin / "src").mkdir()
    (origin / "config").mkdir()
    (origin / "tests").mkdir()
    (origin / "src" / "prod.py").write_text("prod\n")
    (origin / "config" / "settings.yaml").write_text("mode: paper_only\n")
    (origin / "tests" / "keep.py").write_text("keep\n")
    (origin / "tests" / "slice_a.py").write_text("v1\n")
    subprocess.check_call(["git", "add", "."], cwd=origin)
    _git_commit(origin, "base")
    (origin / "tests" / "slice_a.py").write_text("v2\n")
    subprocess.check_call(["git", "add", "tests/slice_a.py"], cwd=origin)
    _git_commit(origin, "slice")
    shallow = tmp_path / "shallow"
    # A path clone ignores --depth. file:// is still local and makes the depth-1 checkout real.
    subprocess.check_call(["git", "clone", "--depth", "1", "-q", origin.resolve().as_uri(), str(shallow)])
    slice_files = {"tests/slice_a.py"}
    digest = hashlib.sha256((shallow / "config" / "settings.yaml").read_bytes()).hexdigest()
    pins = (("settings_sha256", "config/settings.yaml", digest),)
    manifest = _outside_slice_sha256(shallow, slice_files)
    receipt = {
        "closure_claim": "none",
        "source": {"base_head": BASE_HEAD, "base_tree": BASE_TREE},
        "identity": {
            "closure_claim": "none",
            "base_head": BASE_HEAD,
            "base_tree": BASE_TREE,
            "outside_slice_sha256": manifest,
        },
        "inputs": {"settings_sha256": digest},
    }
    return shallow, receipt, pins, manifest, slice_files


def test_r7_7_shallow_checkout_enforces_identity(tmp_path, monkeypatch):
    """Missing base object: the old rev-parse exits 128; pins still fail closed; the replay still runs.

    closure_claim: none. This does not close R7, MD-18, or R6.
    """
    shallow, receipt, pins, manifest, slice_files = _shallow_fixture(tmp_path / "fixture")
    old = subprocess.run(
        ["git", "rev-parse", f"{BASE_HEAD}^{{tree}}"], cwd=shallow, capture_output=True, text=True)
    assert old.returncode == 128
    parent = subprocess.run(["git", "rev-parse", "HEAD^"], cwd=shallow, capture_output=True, text=True)
    assert parent.returncode == 128
    assert _commit_exists(shallow, BASE_HEAD) is False
    kwargs = dict(base_head=BASE_HEAD, base_tree=BASE_TREE, slice_files=slice_files,
                  input_pins=pins, outside_sha256=manifest)
    verify_base_identity(shallow, receipt, **kwargs)

    bad_tree = json.loads(json.dumps(receipt))
    bad_tree["source"]["base_tree"] = "0" * 64
    with pytest.raises(AssertionError):
        verify_base_identity(shallow, bad_tree, **kwargs)
    bad_pin = json.loads(json.dumps(receipt))
    bad_pin["inputs"]["settings_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        verify_base_identity(shallow, bad_pin, **kwargs)
    bad_manifest = json.loads(json.dumps(receipt))
    bad_manifest["identity"]["outside_slice_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        verify_base_identity(shallow, bad_manifest, **kwargs)

    (shallow / "src" / "prod.py").write_text("hacked\n")
    subprocess.check_call(["git", "add", "src/prod.py"], cwd=shallow)
    _git_commit(shallow, "prod edit")
    with pytest.raises(AssertionError):
        verify_base_identity(shallow, receipt, **kwargs)

    # Hide the base object and run the real receipt contract plus the full matrix.
    monkeypatch.setattr(sys.modules[__name__], "_commit_exists", lambda _root, _sha: False)
    assert _commit_exists(ROOT, BASE_HEAD) is False
    assert_receipt_contract(json.loads(RECEIPT.read_text()))
    test_r7_7_failure_replay_receipt(tmp_path / "replay")
