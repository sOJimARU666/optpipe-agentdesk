"""OPT-IN comparison with Doodle's LIVE spec folder (../trading/spec), marker `live_spec`.

The normal suite is hermetic (pinned spec/ only). These tests answer "has the live folder moved since the pin?"
and are skipped unless run with `-m live_spec` or OPTPIPE_LIVE_SPEC_TESTS=1, and always skipped in CI mode
(OPTIONS_SPEC_DIR=spec) or when the live folder is absent. A failure here means: Doodle is mid-edit or has a
new release; it never means the pinned build is broken.

    .venv/bin/python -m pytest -m live_spec tests/test_live_spec.py -rs
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import LIVE_SPEC, PINNED_SPEC

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("sync_spec", ROOT / "scripts" / "sync_spec.py")
sync_spec = importlib.util.module_from_spec(_s)
_s.loader.exec_module(sync_spec)
pytestmark = pytest.mark.live_spec


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.strip().split("."))


def test_live_release_is_self_consistent():
    """Like `sha256sum -c MANIFEST.sha256` in the live folder (manifest list only; clutter/backups ignored)."""
    files = sync_spec.parse_manifest((LIVE_SPEC / "MANIFEST.sha256").read_text())
    bad = [n for n, d in files.items() if not (LIVE_SPEC / n).is_file() or sync_spec.sha256(LIVE_SPEC / n) != d]
    version = (LIVE_SPEC / "VERSION").read_text().strip()
    print(f"live spec VERSION {version}, sha256(MANIFEST) {sync_spec.sha256(LIVE_SPEC / 'MANIFEST.sha256')}")
    assert bad == [], f"live spec {version}: files differ from its own MANIFEST (mid-edit?): {bad}"


def test_live_is_the_pin_or_newer():
    live, pinned = (LIVE_SPEC / "VERSION").read_text().strip(), sync_spec.EXPECTED_VERSION
    assert _ver(live) >= _ver(pinned), f"live spec {live} is older than the pin {pinned}"
    if _ver(live) > _ver(pinned):
        pytest.skip(f"live spec {live} is newer than the pin {pinned}: re-pin pending (Doodle sends VERSION + manifest)")
    assert sync_spec.sha256(LIVE_SPEC / "MANIFEST.sha256") == sync_spec.EXPECTED_MANIFEST_SHA256
    files = sync_spec.parse_manifest((PINNED_SPEC / "MANIFEST.sha256").read_text())
    differ = [n for n in files if (LIVE_SPEC / n).read_bytes() != (PINNED_SPEC / n).read_bytes()]
    assert differ == [], f"live {live} differs from the pinned copy: {differ}"


def test_fixture_run_validates_against_the_live_spec(tmp_path):
    env = {**os.environ, "OPTIONS_SPEC_DIR": str(LIVE_SPEC), "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([str(Path(sys.executable).parent / "optpipe"), "run", "--out", str(tmp_path)], env=env,
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and "OK as 1.1.6" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]
