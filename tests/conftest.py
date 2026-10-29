"""Shared fixtures. Doodle's spec is loaded in place (read-only); spec-dependent tests skip if it is absent."""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True   # never drop .pyc into Doodle's spec folder when importing its modules

from optpipe.config import load_settings  # noqa: E402
from optpipe.spec import discover  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "2026-10-13"

# Hermetic by default: the suite reads ONLY the repo's pinned spec/ (never Doodle's live, possibly mid-edit
# ../trading/spec). Set before any settings are loaded, and inherited by every subprocess the tests start
# (optpipe CLI, gen_models.py, make_spec_patch.py, sync_spec.py). An explicit OPTIONS_SPEC_DIR is respected.
# Comparisons with the live folder are opt-in: tests/test_live_spec.py (marker `live_spec`).
PINNED_SPEC = ROOT / "spec"
LIVE_SPEC = (ROOT.parent / "trading" / "spec").resolve()
USER_SPEC_DIR = os.environ.get("OPTIONS_SPEC_DIR")
os.environ.setdefault("OPTIONS_SPEC_DIR", str(PINNED_SPEC))


@pytest.fixture(scope="session", autouse=True)
def _isolated_state(tmp_path_factory):
    """IV history / HTTP cache never touch the repo's var/ during tests."""
    import os
    base = tmp_path_factory.mktemp("state")
    old = {k: os.environ.get(k) for k in ("OPTPIPE_STATE_DIR", "OPTPIPE_CACHE_DIR")}
    os.environ["OPTPIPE_STATE_DIR"], os.environ["OPTPIPE_CACHE_DIR"] = str(base / "state"), str(base / "cache")
    yield base
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def spec(settings):
    sp = discover(settings.spec_path)
    if sp.path("risk_rules") is None or sp.path("session_schema") is None:
        pytest.skip(f"Doodle's spec not found at {settings.spec_path}")
    return sp


@pytest.fixture(scope="session")
def raw_rules(spec):
    return spec.risk_rules()


@pytest.fixture(scope="session")
def ref(spec):
    return spec.load_module("reference_impl")


@pytest.fixture(scope="session")
def validator(spec):
    return spec.load_module("session_validator")


@pytest.fixture(scope="session")
def example(spec):
    return json.loads(spec.path("session_example").read_text())


@pytest.fixture(scope="session")
def pipeline_output(settings, spec, tmp_path_factory):
    """One full offline run from the 2026-10-13 fixtures, written to a temp dir via the CLI."""
    from optpipe.cli import main
    out = tmp_path_factory.mktemp("out")
    rc = main(["run", "--fixtures", str(FIXTURES), "--out", str(out)])
    rec = json.loads((out / "session-2026-10-13.json").read_text())
    return {"rc": rc, "out": out, "record": rec}


@pytest.fixture
def rec(pipeline_output):
    return copy.deepcopy(pipeline_output["record"])


@pytest.fixture(scope="module")
def ruleset_calendar(spec):
    from optpipe.risk import load_ruleset
    from optpipe.risk.engine import Calendar
    return Calendar(load_ruleset(spec)[0].config.market_hours)


def _live_spec_skip_reason(config) -> str | None:
    if not (os.environ.get("OPTPIPE_LIVE_SPEC_TESTS") == "1" or "live_spec" in (config.getoption("-m") or "")):
        return "live-spec comparison: opt in with `-m live_spec` or OPTPIPE_LIVE_SPEC_TESTS=1"
    if not (LIVE_SPEC / "MANIFEST.sha256").is_file():
        return f"live spec folder absent ({LIVE_SPEC.name} next to the repo)"
    if USER_SPEC_DIR and Path(USER_SPEC_DIR).resolve() == PINNED_SPEC.resolve():
        return "CI mode (OPTIONS_SPEC_DIR=spec): pinned copy only"
    return None


def pytest_collection_modifyitems(config, items):
    """Opt-in markers. `live`: real read-only market-data GETs, run only with `-m live` or OPTPIPE_LIVE_TESTS=1.
    `live_spec`: compare with Doodle's live ../trading/spec, run only with `-m live_spec` or
    OPTPIPE_LIVE_SPEC_TESTS=1, and never in CI mode or when the folder is absent."""
    m = config.getoption("-m") or ""
    live_off = not (os.environ.get("OPTPIPE_LIVE_TESTS") == "1" or re.search(r"\blive\b(?!_)", m))
    spec_reason = _live_spec_skip_reason(config)
    for item in items:
        if "live" in item.keywords and live_off:
            item.add_marker(pytest.mark.skip(reason="live test: run with `-m live` or OPTPIPE_LIVE_TESTS=1"))
        if "live_spec" in item.keywords and spec_reason:
            item.add_marker(pytest.mark.skip(reason=spec_reason))
