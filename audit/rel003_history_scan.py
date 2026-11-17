#!/usr/bin/env python3
"""Offline historical scan with the repository's exact existing SECRET regex.

No dependency installation, fetching, credential checks, or raw-match output.
This bounded audit is not Gitleaks/Betterleaks and cannot close REL-003 alone.
Exit 0: complete scoped scan, no matches; 1: matches require review; 2: incomplete.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

RULE_COMMIT = "d4ad49ad1eae03cc41f144cb4a01bfb81d5fab36"
RULE_FILE = "tests/test_repo_hygiene.py"
RULE_SHA256 = "213ae0df179f41758bad47482dcd0739b919c12a1bc76d8514602ec5678db108"
MAX_OBJECTS = 20000
MAX_OBJECT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
OID = re.compile(r"[0-9a-f]{40}\Z")


class Incomplete(RuntimeError):
    """The audit must not report a complete result when scope is unavailable."""


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    env = os.environ.copy()
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "--no-replace-objects", *args], cwd=repo,
                            env=env, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=60)
    if result.returncode:
        # Git's stderr can contain user-controlled content. Do not publish it.
        raise Incomplete(f"git {args[0]} failed with exit {result.returncode}")
    return result.stdout


def load_rule(repo: Path) -> tuple[re.Pattern, dict]:
    source = git(repo, "show", f"{RULE_COMMIT}:{RULE_FILE}")
    if hashlib.sha256(source).hexdigest() != RULE_SHA256:
        raise Incomplete("Rule-source bytes do not match the audited pin")
    matches = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "SECRET" for t in n.targets)]
    if len(matches) != 1:
        raise Incomplete("Rule assignment missing or ambiguous")
    call = matches[0].value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "re"
            and call.func.attr == "compile" and len(call.args) == 1 and not call.keywords):
        raise Incomplete("Unexpected rule expression")
    pattern = ast.literal_eval(call.args[0])
    return re.compile(pattern), {
        "source_commit": RULE_COMMIT, "source_path": RULE_FILE,
        "source_sha256": RULE_SHA256,
        "pattern_sha256": hashlib.sha256(pattern.encode()).hexdigest(),
        "flags": "Python Unicode regex, unchanged from pinned source",
    }


def read_refs(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) != 2 or not OID.fullmatch(fields[0]):
            raise Incomplete("Malformed frozen ref inventory")
        rows.append({"sha": fields[0], "ref": fields[1]})
    if not rows:
        raise Incomplete("Empty scope is not a clean scan")
    if len({r["ref"] for r in rows}) != len(rows):
        raise Incomplete("Duplicate ref names in inventory")
    return sorted(rows, key=lambda r: r["ref"])


def safe_label(value: str, detector: re.Pattern) -> str:
    value = detector.sub("<redacted>", value)
    value = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "<email-redacted>", value)
    return "".join(c if c.isprintable() else "?" for c in value)


def rule_id(value: str) -> str:
    # Classification happens only after the unchanged detector has matched.
    for prefix, label in (("ghp_", "github-classic"), ("gho_", "github-oauth"),
                          ("github_pat_", "github-fine-grained"), ("AKIA", "aws-access-id"),
                          ("sk-", "sk-prefix"), ("-----BEGIN", "private-key-header"),
                          ("xox", "slack-prefix"), ("eyJ", "jwt-shaped")):
        if value.startswith(prefix):
            return label
    return "assigned-credential"


def detect(data: bytes, detector: re.Pattern) -> list[dict]:
    # Preserve ASCII matches in binary/non-UTF8 files; do not silently skip them.
    text = data.decode("utf-8", errors="surrogateescape")
    return [{"line": text.count("\n", 0, m.start()) + 1, "rule": rule_id(m.group()),
             "ordinal": i + 1} for i, m in enumerate(detector.finditer(text))]


def set_digest(values: set[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(values)) + "\n").encode()).hexdigest()


def scan(repo: Path, refs: list[dict], detector: re.Pattern, *,
         max_object_bytes: int = MAX_OBJECT_BYTES) -> dict:
    if git(repo, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise Incomplete("Shallow repository cannot establish this history scope")
    roots = sorted({r["sha"] for r in refs})
    if not roots or any(not OID.fullmatch(sha) for sha in roots):
        raise Incomplete("Invalid or empty object roots")
    root_data = ("\n".join(roots) + "\n").encode()
    # Frozen SHAs, not moving refs or --all (which could include local experiments).
    objects = set(git(repo, "rev-list", "--objects", "--no-object-names", "--stdin",
                      data=root_data).decode().splitlines())
    if len(objects) > MAX_OBJECTS:
        raise Incomplete("Object-count bound exceeded")
    metadata = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                   data=("\n".join(sorted(objects)) + "\n").encode()).decode().splitlines()
    entries = []
    for row in metadata:
        parts = row.split()
        if len(parts) != 3 or parts[1] not in {"commit", "tree", "blob", "tag"}:
            raise Incomplete("Missing or unrecognized reachable Git object")
        entries.append((parts[0], parts[1], int(parts[2])))
    selected = [e for e in entries if e[1] in {"blob", "commit", "tag"}]
    if any(size > max_object_bytes for _, _, size in selected):
        raise Incomplete("Per-object byte bound exceeded; no clean result")
    if sum(size for _, _, size in selected) > MAX_TOTAL_BYTES:
        raise Incomplete("Total byte bound exceeded")

    # Independent enumeration: union of every commit's complete file tree.
    commits = sorted(e[0] for e in entries if e[1] == "commit")
    snapshot_blobs = set()
    locations: dict[str, set[tuple[str, str]]] = {}
    path_findings = []
    gitlinks = set()
    for commit in commits:
        for entry in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
            if not entry:
                continue
            metadata_b, path_b = entry.split(b"\t", 1)
            _mode, kind, obj = metadata_b.decode().split()
            path = path_b.decode("utf-8", errors="surrogateescape")
            if kind == "commit":
                gitlinks.add(obj)
                continue
            if kind != "blob":
                raise Incomplete("Unexpected recursive tree entry")
            snapshot_blobs.add(obj)
            locations.setdefault(obj, set()).add((commit, safe_label(path, detector)))
            for hit in detect(path_b, detector):
                path_findings.append({"kind": "path", "commit": commit,
                                      "path": safe_label(path, detector), **hit})
    object_blobs = {sha for sha, kind, _ in entries if kind == "blob"}
    if snapshot_blobs != object_blobs:
        raise Incomplete("Independent blob inventories disagree")
    if gitlinks:
        raise Incomplete("Submodule content is not included; explicit scoping required")

    findings = []
    non_utf8 = 0
    nul_containing = 0
    for sha, kind, size in selected:
        raw = git(repo, "cat-file", kind, sha)
        if len(raw) != size:
            raise Incomplete("Object length disagrees with Git metadata")
        if kind == "blob":
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                non_utf8 += 1
            nul_containing += int(b"\0" in raw)
        hits = detect(raw, detector)
        if hits:
            locs = sorted(locations.get(sha, set()))
            findings.append({"object": sha, "kind": kind, "matches": hits,
                             "locations": [{"commit": c, "path": p} for c, p in locs]})
    for row in refs:
        for hit in detect(row["ref"].encode(), detector):
            path_findings.append({"kind": "ref", "object": row["sha"],
                                  "ref": safe_label(row["ref"], detector), **hit})
    match_count = sum(len(f["matches"]) for f in findings) + len(path_findings)
    return {
        "complete_for_declared_scope": True, "closure_claim": "none",
        "scope": [{"sha": r["sha"], "ref": safe_label(r["ref"], detector)} for r in refs],
        "unique_roots": len(roots), "object_counts": dict(Counter(k for _, k, _ in entries)),
        "scanned_content_bytes": sum(size for _, _, size in selected),
        "commits": commits, "commit_set_sha256": set_digest(set(commits)),
        "blob_set_sha256": set_digest(object_blobs),
        "independent_snapshot_blob_count": len(snapshot_blobs),
        "independent_snapshot_set_sha256": set_digest(snapshot_blobs),
        "independent_inventory_agrees": True,
        "non_utf8_blobs_scanned": non_utf8, "nul_containing_blobs_scanned": nul_containing,
        "gitlinks": 0, "skipped_objects": 0, "findings": findings,
        "path_or_ref_findings": path_findings, "match_count": match_count,
        "match_values_emitted": False,
        "limits": {"max_objects": MAX_OBJECTS, "max_object_bytes": max_object_bytes,
                   "max_total_bytes": MAX_TOTAL_BYTES},
        "exclusions": ["unadvertised or unreachable history", "reflogs and working-tree files",
                       "external submodule/LFS payloads", "decoded or decompressed content",
                       "credential classes outside the pinned regex"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--refs", type=Path, required=True)
    args = parser.parse_args()
    try:
        detector, pin = load_rule(args.repo)
        result = scan(args.repo, read_refs(args.refs), detector)
        result.update(rule=pin, python=platform.python_version(),
                      git=git(args.repo, "--version").decode().strip(),
                      scope_file_sha256=hashlib.sha256(args.refs.read_bytes()).hexdigest(),
                      scanner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result["match_count"] else 0
    except (Incomplete, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        # Exception types are enough for a shareable failure receipt; no raw text.
        print(json.dumps({"complete_for_declared_scope": False, "closure_claim": "none",
                          "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, Incomplete) else "audit failed"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
