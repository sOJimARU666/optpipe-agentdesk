"""claude_code/ is outside src/ and scripts/, so tests/test_paper_only.py does not scan it. This test applies the SAME
scanner (loaded from that file, not copied) to every .py in this folder: no broker SDK, no order-like names, GET-only
network, no wildcard bind, no inline `python -c/-m` or network tool in a subprocess, no shell=True. Paper only.
Hygiene (absolute paths, secrets, emails) is already covered: tests/test_repo_hygiene.py scans every tracked file.
Scope: every .py here, test files included (stricter than the main scan, which skips tests/), except this harness
file itself, which must name the rules (e.g. ORDER_NAME). tests/test_paper_only.py does not scan itself either.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def _load_paper_only():
    spec = importlib.util.spec_from_file_location("_shared_paper_only", ROOT / "tests" / "test_paper_only.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pol = _load_paper_only()
SOURCES = sorted(p for p in HERE.rglob("*.py") if "__pycache__" not in p.parts and p != Path(__file__).resolve())


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def test_folder_sources_found():
    rels = {_rel(p) for p in SOURCES}
    assert {"claude_code/conftest.py", "claude_code/CC-001/check_fixture_repro.py",
            "claude_code/CC-001/test_fixture_repro.py"} <= rels
    assert "claude_code/test_folder_rules.py" not in rels


def test_folder_passes_the_shared_paper_only_scanner():
    problems = []
    for p in SOURCES:
        text, rel = p.read_text(encoding="utf-8"), _rel(p)
        tree = ast.parse(text, filename=rel)
        problems += pol.scan_network(text, rel) + pol.scan_binds(tree, rel)
        if "0.0.0.0" in text:
            problems.append(f"{rel}: all-interfaces literal")
        for n in ast.walk(tree):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            problems += [f"{rel}: imports broker SDK {m}" for m in names if m.split(".")[0] in pol.BROKER_MODULES]
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and pol.ORDER_NAME.search(n.name):
                problems.append(f"{rel}:{n.lineno} defines {n.name}")
            if isinstance(n, ast.Attribute) and pol.ORDER_NAME.search(n.attr):
                problems.append(f"{rel}:{n.lineno} uses .{n.attr}")
    assert problems == []


def test_scanner_still_bites_inside_this_folder():
    """Control: a folder path gets no exemption from the shared scanner."""
    rel = "claude_code/CC-001/x.py"
    assert pol.scan_network("import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'x'])\n", rel)
    assert pol.scan_network("import requests\nrequests.post('http://127.0.0.1:9')\n", rel)
    assert pol.scan_network("import subprocess\nsubprocess.run(['curl', 'http://x'])\n", rel)
