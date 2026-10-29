"""Shared fixtures. Doodle's spec is loaded in place (read-only); spec-dependent tests skip if it is absent."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True   # never drop .pyc into Doodle's spec folder when importing its modules

from optpipe.config import load_settings  # noqa: E402
from optpipe.spec import discover  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "2026-10-13"


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
