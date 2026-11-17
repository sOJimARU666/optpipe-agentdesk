#!/usr/bin/env python3
"""Bounded, offline static audit of a frozen Git commit using only Python's stdlib.

This is NOT Ruff and does not close REL-004. Never imports or runs target code.
Exit 0: no candidates in the stated scope; 1: review candidates; 2: incomplete.
"""
from __future__ import annotations

import argparse
import ast
import builtins
from collections import Counter, defaultdict
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import symtable
import sys
import tokenize
import warnings

ROOTS = ("src", "tests", "scripts")
MAX_FILES = 2000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
RULES = ("compile-error", "compiler-warning", "python311-grammar",
         "unbound-global-candidate", "duplicate-definition-candidate",
         "duplicate-literal-key", "nonempty-tuple-assert")
IMPLICIT_GLOBALS = {"__file__", "__name__", "__package__", "__doc__", "__spec__",
                    "__loader__", "__cached__", "__builtins__", "__path__"}


class Incomplete(RuntimeError):
    """Input or execution cannot establish the declared static scope."""


def git(repo: Path, *args: str) -> bytes:
    # Same offline Git precautions as Pilot 002. No fetch, shell, or target import.
    env = os.environ.copy()
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "--no-replace-objects", *args], cwd=repo, env=env,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise Incomplete(f"git {args[0]} failed with exit {result.returncode}")
    return result.stdout


def analyze(source: bytes, path: str) -> dict:
    findings, limitations = [], []

    def add(rule: str, node=None, **extra):
        findings.append({"rule": rule, "path": path,
                         "line": getattr(node, "lineno", 1) or 1, **extra})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            compile(source, path, "exec", dont_inherit=True, optimize=0)
        except (SyntaxError, ValueError, UnicodeError) as error:
            add("compile-error", error, error_type=type(error).__name__)
            return {"findings": findings, "limitations": limitations,
                    "ast_analyzed": False}
    for warning in caught:
        # No source lines, literal values, or exception messages are published.
        findings.append({"rule": "compiler-warning", "path": path,
                         "line": warning.lineno, "category": warning.category.__name__})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # compile diagnostics already captured above
        tree = ast.parse(source, filename=path)
        try:
            ast.parse(source, filename=path, feature_version=(3, 11))
        except SyntaxError as error:
            add("python311-grammar", error)
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        symbols = symtable.symtable(source.decode(encoding), path, "exec")

    bound = {s.get_name() for s in symbols.get_symbols()
             if s.is_assigned() or s.is_imported() or s.is_namespace()}
    known = bound | set(dir(builtins)) | IMPLICIT_GLOBALS
    has_star = any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
                   for n in ast.walk(tree))
    if has_star:
        limitations.append({"path": path, "check": "unbound-global-candidate",
                            "reason": "star import prevents closed global-name inventory"})
    else:
        def visit_scope(scope):
            for symbol in scope.get_symbols():
                if symbol.is_referenced() and symbol.is_global() and symbol.get_name() not in known:
                    findings.append({"rule": "unbound-global-candidate", "path": path,
                                     "line": scope.get_lineno() or 1,
                                     "scope": scope.get_name(), "name": symbol.get_name()})
            for child in scope.get_children():
                visit_scope(child)
        visit_scope(symbols)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef)):
            definitions = defaultdict(list)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    definitions[child.name].append(child)
            for name, definitions_for_name in definitions.items():
                for duplicate in definitions_for_name[1:]:
                    add("duplicate-definition-candidate", duplicate, name=name,
                        first_line=definitions_for_name[0].lineno)
        if isinstance(node, ast.Dict):
            keys = {}
            for key in node.keys:
                if key is None:
                    continue  # **mapping requires runtime values
                try:
                    value = ast.literal_eval(key)
                    hash(value)
                except (ValueError, TypeError):
                    continue
                if value in keys:
                    add("duplicate-literal-key", key, first_line=keys[value])
                keys[value] = key.lineno
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Tuple) and node.test.elts:
            add("nonempty-tuple-assert", node)
    return {"findings": findings, "limitations": limitations, "ast_analyzed": True}


def scan(repo: Path, revision: str, *, max_file_bytes: int = MAX_FILE_BYTES) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Incomplete("A full 40-character commit SHA is required")
    if git(repo, "cat-file", "-t", revision).strip() != b"commit":
        raise Incomplete("Revision is not a commit")
    entries = []
    for row in git(repo, "ls-tree", "-rlz", revision, "--", *ROOTS).split(b"\0"):
        if not row:
            continue
        meta, raw_path = row.split(b"\t", 1)
        mode, kind, oid, size = meta.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if kind == "commit":
            raise Incomplete("Submodule content in scope requires separate scoping")
        if not path.endswith(".py"):
            continue
        if mode not in {"100644", "100755"} or kind != "blob":
            raise Incomplete("Non-regular Python entry in scope")
        entries.append({"path": path, "git_blob": oid, "bytes": int(size)})
    if not entries or len(entries) > MAX_FILES:
        raise Incomplete("Empty scope or file-count bound exceeded")
    if any(e["bytes"] > max_file_bytes for e in entries):
        raise Incomplete("Per-file bound exceeded")
    if sum(e["bytes"] for e in entries) > MAX_TOTAL_BYTES:
        raise Incomplete("Total-byte bound exceeded")
    findings, limitations, inventory = [], [], []
    ast_files = 0
    for entry in sorted(entries, key=lambda e: e["path"]):
        source = git(repo, "cat-file", "blob", entry["git_blob"])
        if len(source) != entry["bytes"]:
            raise Incomplete("Blob-size mismatch")
        result = analyze(source, entry["path"])
        inventory.append({**entry, "sha256": hashlib.sha256(source).hexdigest()})
        findings.extend(result["findings"])
        limitations.extend(result["limitations"])
        ast_files += result["ast_analyzed"]
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"format": 1, "engine": "CPython stdlib bounded static audit",
            "python": platform.python_version(), "revision": revision,
            "tree": git(repo, "rev-parse", f"{revision}^{{tree}}").decode().strip(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "ruff": "NOT_RUN", "closure_claim": "none", "complete_scope": True,
            "rules": list(RULES), "scope_roots": list(ROOTS),
            "files": len(inventory), "ast_files": ast_files,
            "files_by_root": dict(sorted(Counter(e["path"].split("/")[0] for e in inventory).items())),
            "bytes": sum(e["bytes"] for e in inventory),
            "inventory_sha256": digest, "inventory": inventory,
            "findings": sorted(findings, key=lambda f: (f["path"], f["line"], f["rule"])),
            "limitations": limitations}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    try:
        report = scan(args.repo, args.revision)
    except (Incomplete, OSError, UnicodeError, RecursionError, subprocess.TimeoutExpired) as error:
        # Never convert incomplete execution to a clean baseline.
        print(json.dumps({"complete_scope": False, "closure_claim": "none",
                          "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, Incomplete) else "audit execution failed"},
                         sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
