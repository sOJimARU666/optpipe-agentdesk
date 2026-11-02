"""pytest: schema v1.1 additions (closed phase / INV-10, trace_ref / INV-11, integer open_interest) + v1 backward compatibility."""
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

import risk_reference as rr
import validate_session as vs

SPEC = Path(__file__).resolve().parent
EXAMPLE = json.loads((SPEC / "session.example.json").read_text())
CAL = rr.Calendar(rr.load_rules()["config"]["market_hours"])


def strip_v1_1(s: dict) -> dict:
    """Drop 1.1.0-only fields (per-run arrays, raw tick fields) so the record is a plain 1.0.0 shape."""
    s.pop("premarket_scans", None); s.pop("global_snapshots", None); s.pop("trace_ref", None)
    for _, o in vs._walk(s):
        if isinstance(o, dict):
            for k in ("raw_amount", "raw_value", "tick_size", "rounding"):
                o.pop(k, None)
    return requality(s)


def requality(s: dict) -> dict:
    """data_quality lists every non-ok field path, so regenerate it after structural edits."""
    s["data_quality"].update(vs.quality_index(s))
    return s


def _with_run(ny_wall: str, phase: str, version: str = "1.1.0") -> dict:
    s = copy.deepcopy(EXAMPLE)
    s["schema_version"] = version
    if version == "1.0.0":
        strip_v1_1(s)
    r = copy.deepcopy(s["runs"][0])
    r.update(run_id="run-extra", phase=phase, started=rr.tspair(datetime.fromisoformat(ny_wall).replace(tzinfo=rr.NY)))
    s["runs"].append(r)
    return s


def test_example_is_v1_1():
    assert EXAMPLE["schema_version"] == "1.1.2"
    assert vs.validate(EXAMPLE) == []


def test_v1_backup_example_still_valid():
    v1 = json.loads((SPEC / "_v1_backup" / "session.example.json").read_text())
    assert v1["schema_version"] == "1.0.0"
    assert vs.validate(v1) == []


def test_example_open_interest_is_int():
    vals = [k["open_interest"]["value"] for c in EXAMPLE["candidates"] for sn in c["snapshots"]
            for sl in sn["option_chain_slices"] for k in sl["contracts"]]
    assert vals and all(v is None or type(v) is int for v in vals)


def test_fractional_open_interest_rejected():
    s = copy.deepcopy(EXAMPLE)
    s["candidates"][0]["snapshots"][0]["option_chain_slices"][0]["contracts"][0]["open_interest"]["value"] = 472.5
    assert any("open_interest" in e for e in vs.validate(s))


@pytest.mark.parametrize("ny_wall,closed", [
    ("2026-10-13 03:59", True), ("2026-10-13 04:00", False), ("2026-10-13 15:59", False), ("2026-10-13 16:00", True),
    ("2026-10-17 11:00", True),            # Saturday
    ("2026-11-26 11:00", True),            # Thanksgiving
    ("2026-11-27 12:59", False), ("2026-11-27 13:00", True),  # early close
    ("2026-11-01 01:30", True),            # US DST fall-back night (Sunday)
])
def test_calendar_is_closed(ny_wall, closed):
    assert CAL.is_closed(datetime.fromisoformat(ny_wall).replace(tzinfo=rr.NY)) is closed


@pytest.mark.parametrize("ny_wall", ["2026-10-13 02:00", "2026-10-13 18:30", "2026-10-17 10:00", "2026-11-26 10:00", "2026-11-27 13:30"])
def test_closed_phase_accepted(ny_wall):
    assert vs.validate(_with_run(ny_wall, "closed")) == []


@pytest.mark.parametrize("ny_wall,phase,version", [
    ("2026-10-13 10:00", "closed", "1.1.0"),       # closed while market open
    ("2026-11-27 12:00", "closed", "1.1.0"),       # before early close
    ("2026-10-13 02:00", "closed", "1.0.0"),       # closed needs v1.1
    ("2026-10-13 03:00", "premarket", "1.1.0"),    # must be closed in v1.1
    ("2026-10-13 17:00", "afterhours", "1.1.0"),   # afterhours is v1-only
    ("2026-10-17 10:00", "intraday", "1.1.0"),     # weekend
])
def test_inv10_rejects(ny_wall, phase, version):
    assert any(e.startswith("INV-10") for e in vs.validate(_with_run(ny_wall, phase, version)))


def test_v1_afterhours_still_valid():
    assert vs.validate(_with_run("2026-10-13 17:00", "afterhours", "1.0.0")) == []


def test_trace_ref_ok_and_hash_checked(tmp_path):
    (tmp_path / "session-x.trace.json").write_text('{"trace": []}\n')
    s = copy.deepcopy(EXAMPLE)
    s["trace_ref"] = {"path": "session-x.trace.json", "sha256": hashlib.sha256((tmp_path / "session-x.trace.json").read_bytes()).hexdigest()}
    assert vs.validate(s) == []                    # no base_dir -> shape only
    assert vs.validate(s, base_dir=tmp_path) == []
    s["trace_ref"]["sha256"] = "0" * 64
    assert any(e.startswith("INV-11") for e in vs.validate(s, base_dir=tmp_path))
    s["trace_ref"]["path"] = "missing.trace.json"
    assert any("not found" in e for e in vs.validate(s, base_dir=tmp_path))


@pytest.mark.parametrize("tr", [
    {"path": "/abs/x.trace.json", "sha256": "a" * 64},
    {"path": "../x.trace.json", "sha256": "a" * 64},
    {"path": "x.json", "sha256": "a" * 64},
    {"path": "x.trace.json", "sha256": "A" * 64},
    {"path": "x.trace.json", "sha256": "a" * 64, "extra": 1},
    {"path": "x.trace.json"},
])
def test_trace_ref_shape_rejected(tr):
    s = copy.deepcopy(EXAMPLE); s["trace_ref"] = tr
    assert any(e.startswith("schema trace_ref") for e in vs.validate(s))


def test_trace_ref_requires_v1_1():
    s = copy.deepcopy(EXAMPLE); s["schema_version"] = "1.0.0"
    s["trace_ref"] = {"path": "x.trace.json", "sha256": "a" * 64}
    assert any(e.startswith("INV-11") for e in vs.validate(s))
