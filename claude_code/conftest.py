"""Test setup for claude_code/ only. The main suite (testpaths = tests) does not collect this folder.

Same hermetic rules as tests/conftest.py, which this folder does not load: the pinned spec/ (unless OPTIONS_SPEC_DIR
is set), the runtime network guard in this process and in every Python subprocess (tests/netguard on PYTHONPATH,
OPTPIPE_NETGUARD=1), and IV state / HTTP cache in temp dirs, never the repo's var/. Paper only.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("OPTIONS_SPEC_DIR", str(ROOT / "spec"))

NETGUARD_DIR = ROOT / "tests" / "netguard"
sys.path.insert(0, str(NETGUARD_DIR))
import optpipe_netguard as netguard  # noqa: E402

netguard.install()                                   # idempotent; also safe if tests/conftest.py ran first
os.environ["OPTPIPE_NETGUARD"] = "1"
if str(NETGUARD_DIR) not in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(NETGUARD_DIR), os.environ.get("PYTHONPATH")) if p)
if not os.environ.get("OPTPIPE_NETGUARD_LOG"):
    _fd, _log = tempfile.mkstemp(prefix="optpipe-netguard-", suffix=".log")
    os.close(_fd)
    os.environ["OPTPIPE_NETGUARD_LOG"] = _log


@pytest.fixture(scope="session", autouse=True)
def _claude_code_isolated_state(tmp_path_factory):
    """IV history and HTTP cache never touch the repo's var/."""
    base = tmp_path_factory.mktemp("cc-state")
    old = {k: os.environ.get(k) for k in ("OPTPIPE_STATE_DIR", "OPTPIPE_CACHE_DIR")}
    os.environ["OPTPIPE_STATE_DIR"], os.environ["OPTPIPE_CACHE_DIR"] = str(base / "state"), str(base / "cache")
    yield base
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
