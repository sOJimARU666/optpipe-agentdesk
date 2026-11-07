#!/usr/bin/env python3
"""Reproduce PR #31's integration identity failure using only Git and stdlib.

Run from a clone that already contains the pinned commits (no fetching):
    python audit/work_pilot_001_reproduce.py

This executes the original, hash-pinned identity helper, not the full pytest
replay. It also independently computes the manifest from committed tree objects.
Only disposable detached worktrees and an unreferenced synthetic commit are
created; no branch ref, source file, receipt, dependency, or policy is changed.
Exit 0 means the expected pass/fail/pass reproduction was observed, NOT that the
integration passed. The JSON records the integration rejection explicitly.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import traceback

RELIABILITY = "d4ad49ad1eae03cc41f144cb4a01bfb81d5fab36"
PR_HEAD = "26d7aa57666c55df4f77fea82c5aae9d832d1788"
MAIN = "ae95c68cc10240fe75b41cc30272c463d960f5e3"
BASE = "7ee665444eb4d3197effb95347af9bfbf9b7d787"
MERGED_TREE = "951bced2263d94b9e75441b9d1701be0f4d46a12"
TEST_PATH = "tests/test_r7_7_failure_replay.py"
TEST_SHA256 = "5e9307e0d40e8fe71ab697f5dd4b2e0fd2ff2cca644588b33269c6eb5b7d0061"
RECEIPT_PATH = "tests/receipts/r7_item7_deterministic_replay.json"
FUNCTIONS = {
    "_git", "_commit_exists", "_outside_slice_sha256", "_changed_against_head",
    "verify_base_identity",
}
CONSTANTS = {
    "BASE_HEAD", "BASE_TREE", "OUTSIDE_SLICE_SHA256", "INPUT_PINS",
    "SLICE_FILES", "_FORBIDDEN_PREFIXES",
}


def git(root: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, env=env, capture_output=True, text=True,
        check=True, timeout=60,
    )
    return result.stdout.strip()


def load_original_helper(root: Path) -> dict:
    """Compile exact AST nodes from verified source, without app/pytest imports."""
    raw = (root / TEST_PATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == TEST_SHA256
    nodes = ast.parse(raw, filename=TEST_PATH).body
    selected = []
    found = set()
    for node in nodes:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            selected.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & CONSTANTS:
                selected.append(node)
                found.update(names)
    assert found == FUNCTIONS | CONSTANTS
    namespace = {"Path": Path, "subprocess": subprocess, "hashlib": hashlib}
    exec(compile(ast.Module(body=selected, type_ignores=[]), TEST_PATH, "exec"), namespace)
    return namespace


def committed_manifest(root: Path, excluded: set[str]) -> str:
    """Independent oracle: tree blobs, rather than the helper's index listing."""
    data = subprocess.check_output(["git", "ls-tree", "-rz", "HEAD"], cwd=root)
    lines = []
    for entry in data.split(b"\0"):
        if not entry:
            continue
        metadata, path_bytes = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode().split()
        assert kind == "blob", "This pinned audit expects no submodules"
        path = path_bytes.decode()
        if path not in excluded:
            lines.append(f"{mode} {blob} {path}")
    return hashlib.sha256(("\n".join(sorted(lines)) + "\n").encode()).hexdigest()


def examine(root: Path, label: str) -> dict:
    assert git(root, "status", "--porcelain", "--untracked-files=all") == ""
    helper = load_original_helper(root)
    receipt = json.loads((root / RECEIPT_PATH).read_text())
    pins = [
        {"path": path, "expected": want,
         "actual": hashlib.sha256((root / path).read_bytes()).hexdigest()}
        for _field, path, want in helper["INPUT_PINS"]
    ]
    assert all(pin["expected"] == pin["actual"] for pin in pins)
    committed = committed_manifest(root, helper["SLICE_FILES"])
    indexed = helper["_outside_slice_sha256"](root, helper["SLICE_FILES"])
    assert committed == indexed
    failure = None
    try:
        helper["verify_base_identity"](
            root, receipt, base_head=helper["BASE_HEAD"], base_tree=helper["BASE_TREE"],
            slice_files=helper["SLICE_FILES"], input_pins=helper["INPUT_PINS"],
            outside_sha256=helper["OUTSIDE_SLICE_SHA256"],
        )
    except AssertionError:
        frame = traceback.extract_tb(sys.exc_info()[2])[-1]
        source = (root / TEST_PATH).read_text().splitlines()[frame.lineno - 1].strip()
        failure = {"exception": "AssertionError", "file": TEST_PATH,
                   "line": frame.lineno, "expression": source}
    outside_diff = sorted(set(git(root, "diff", "--name-only", BASE, "HEAD").splitlines())
                          - helper["SLICE_FILES"])
    assert git(root, "status", "--porcelain", "--untracked-files=all") == ""
    return {
        "label": label, "head": git(root, "rev-parse", "HEAD"),
        "tree": git(root, "rev-parse", "HEAD^{tree}"), "worktree_clean": True,
        "test_sha256": TEST_SHA256,
        "receipt_sha256": hashlib.sha256((root / RECEIPT_PATH).read_bytes()).hexdigest(),
        "input_pins": pins, "expected_manifest": helper["OUTSIDE_SLICE_SHA256"],
        "index_manifest": indexed, "independent_tree_manifest": committed,
        "outside_slice_changed_paths": outside_diff,
        "identity_accepted": failure is None, "failure": failure,
    }


def main() -> int:
    if not __debug__:
        raise RuntimeError("Run without Python optimization; original assertions must execute")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    repo = args.repo.resolve()
    for sha in (RELIABILITY, PR_HEAD, MAIN, BASE):
        assert git(repo, "rev-parse", f"{sha}^{{commit}}") == sha
    assert git(repo, "merge-base", MAIN, PR_HEAD) == BASE
    tree = git(repo, "merge-tree", "--write-tree", MAIN, PR_HEAD)
    assert tree == MERGED_TREE, tree
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "Sol Audit", "GIT_AUTHOR_EMAIL": "sol@example.com",
        "GIT_COMMITTER_NAME": "Sol Audit", "GIT_COMMITTER_EMAIL": "sol@example.com",
        "GIT_AUTHOR_DATE": "2026-11-06T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-11-06T00:00:00Z",
    })
    candidate = git(repo, "-c", "commit.gpgsign=false", "commit-tree", tree,
                    "-p", MAIN, "-p", PR_HEAD,
                    "-m", "Synthetic Work Pilot 001 integration; never publish", env=env)
    observations = []
    with tempfile.TemporaryDirectory(prefix="optpipe-pilot001-") as temp:
        created = []
        try:
            for label, sha in (("pr31_before", PR_HEAD), ("current_main_plus_pr31", candidate),
                               ("pr31_after", PR_HEAD)):
                root = Path(temp) / label
                git(repo, "worktree", "add", "--detach", str(root), sha)
                created.append(root)
                observations.append(examine(root, label))
        finally:
            for root in reversed(created):
                git(repo, "worktree", "remove", str(root))
    assert [x["identity_accepted"] for x in observations] == [True, False, True]
    assert observations[1]["failure"]["expression"] == (
        "assert _outside_slice_sha256(root, slice_files) == outside_sha256")
    assert len({x["receipt_sha256"] for x in observations}) == 1
    print(json.dumps({
        "audit": "SOL AI LAB WORK PILOT 001", "closure_claim": "none",
        "mode": "original_identity_helper_and_independent_tree_oracle",
        "pytest_executed_by_this_script": False,
        "python": platform.python_version(), "git": git(repo, "--version"),
        "reliability_sha": RELIABILITY, "main_sha": MAIN, "pr31_sha": PR_HEAD,
        "base_sha": BASE, "integration_tree": tree,
        "synthetic_commit": candidate, "synthetic_commit_published": False,
        "observations": observations, "expected_regression_reproduced": True,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
