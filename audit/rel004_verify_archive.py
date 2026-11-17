#!/usr/bin/env python3
"""Cross-check a REL-004 inventory via git archive, py_compile, and tabnanny.

Independent enumeration/compilation entry point; still the same CPython engine,
not another linter or a second-agent review. Does not execute repository code.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tabnanny
import tarfile
import tempfile
import tokenize
import warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())
    env = os.environ.copy()
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    revision = baseline["revision"]
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Expected full commit SHA")
    top_level = subprocess.run(["git", "--no-replace-objects", "ls-tree", "--name-only", "-z", revision],
                               cwd=args.repo, env=env, capture_output=True, timeout=60, check=True)
    present = set(top_level.stdout.decode().split("\0"))
    roots = [name for name in ("src", "tests", "scripts") if name in present]
    if not roots:
        raise ValueError("Empty static scope")
    archive = subprocess.run(["git", "--no-replace-objects", "archive", "--format=tar",
                              revision, *roots],
                             cwd=args.repo, env=env, capture_output=True, timeout=60, check=True)
    inventory, diagnostics = [], []
    with tempfile.TemporaryDirectory() as temporary, tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        for member in tar:
            if not member.name.endswith(".py"):
                continue
            if not member.isfile():
                raise ValueError("Non-regular Python archive member")
            source = tar.extractfile(member).read()
            ordinal = len(inventory)
            source_path = Path(temporary) / f"source_{ordinal}.py"
            source_path.write_bytes(source)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    py_compile.compile(str(source_path), cfile=str(source_path.with_suffix(".pyc")),
                                       dfile=member.name, doraise=True, optimize=0)
                except py_compile.PyCompileError:
                    diagnostics.append({"path": member.name, "check": "py_compile", "result": "failed"})
            for warning in caught:
                diagnostics.append({"path": member.name, "check": "py_compile_warning",
                                    "line": warning.lineno, "category": warning.category.__name__})
            try:
                tabnanny.process_tokens(tokenize.tokenize(io.BytesIO(source).readline))
            except (tabnanny.NannyNag, tokenize.TokenError, IndentationError, SyntaxError):
                diagnostics.append({"path": member.name, "check": "tabnanny", "result": "failed"})
            inventory.append({"path": member.name, "bytes": len(source),
                              "git_blob": hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest(),
                              "sha256": hashlib.sha256(source).hexdigest()})
    inventory.sort(key=lambda entry: entry["path"])
    matches = inventory == baseline["inventory"]
    report = {"revision": revision, "files": len(inventory), "inventory_matches": matches,
              "inventory_sha256": hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
              "checks": ["git archive inventory vs git ls-tree/cat-file inventory", "py_compile", "tabnanny"],
              "diagnostics": diagnostics, "target_code_executed": False,
              "temporary_bytecode_removed": True}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if matches and inventory and not diagnostics else 1


if __name__ == "__main__":
    sys.exit(main())
