"""Opt-in LIVE test (marker `live`): skipped by default and in CI.

Run it with   .venv/bin/python -m pytest -m live tests/test_watch_live.py
          or  OPTPIPE_LIVE_TESTS=1 .venv/bin/python -m pytest tests/test_watch_live.py

It does one `optpipe watch --live --once` pass over the fixture session: real clock, read-only GETs to Nasdaq's
public quote endpoints, no orders (there is no order path), no notifications. Results depend on the market and
the network, so it only checks the paper-only contract and that the watch document validates.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "2026-10-13"


@pytest.mark.live
def test_watch_live_once_is_paper_only(tmp_path):
    from optpipe.cli import main
    out = tmp_path / "out"
    assert main(["run", "--fixtures", str(FIXTURES), "--out", str(out)]) == 0
    r = subprocess.run([str(Path(sys.executable).parent / "optpipe"), "watch", "--live", "--once",
                        "--session", str(out / "session-2026-10-13.json"), "--out", str(out)],
                       capture_output=True, text=True, cwd=ROOT, timeout=300)
    assert r.returncode in (0, 1), r.stderr[-2000:]
    assert r.stdout.startswith("PAPER ONLY") and "(GET only)" in r.stdout
    docs = [p for p in out.glob("*watch*.json")]
    assert docs, "live watch wrote no watch document"
    doc = json.loads(docs[0].read_text())
    assert doc["mode"]["clock"] == "real"
    from optpipe.watcher.runtime import validate_watch, verify_sidecar
    assert validate_watch(doc) == [] and verify_sidecar(docs[0])
