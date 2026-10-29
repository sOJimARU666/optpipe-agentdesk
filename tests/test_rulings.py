"""Doodle's rulings (2026-10-29): review ids, tick rounding, per-run scans, closed phase, both schemas,
trace hash, generated code/patch in sync. Since Doodle's spec 1.1.0 (merged + pinned 2026-10-29) the record itself is
v1.1-shape (now 1.1.1) with trace_ref; compat.to_v1 remains for v1 readers."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from optpipe.planner.ticks import TickRule
from optpipe.records.compat import to_v1

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = str(ROOT / "tests" / "fixtures" / "2026-10-13")
PATCH = ROOT / "spec_patches" / "v1.1"


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory):
    from optpipe.cli import main
    out = tmp_path_factory.mktemp("two")
    assert main(["run", "--fixtures", FIXTURE_DIR, "--out", str(out)]) == 0
    first = json.loads((out / "session-2026-10-13.json").read_text())
    assert main(["run", "--fixtures", FIXTURE_DIR, "--out", str(out), "--append",
                 "--as-of", "2026-10-13T09:40:00-04:00"]) == 0
    v11 = json.loads((out / "session-2026-10-13.json").read_text())
    assert not (out / "session-2026-10-13.v1_1.json").exists(), "the preview file is superseded by the 1.1.x record"
    return {"out": out, "first": first, "v1": to_v1(v11), "v11": v11}


# ---- ruling 2: review ids unique and stable -------------------------------------------------
def test_review_ids_unique_within_session(two_runs):
    ids = [r["review_id"] for r in two_runs["v11"]["risk_reviews"]]
    assert len(ids) == len(set(ids)) and len(ids) == 2 * len(two_runs["first"]["risk_reviews"])


def test_review_ids_stable_across_append(two_runs):
    before = {r["review_id"]: (r["ticket_id"], r["run_id"]) for r in two_runs["first"]["risk_reviews"]}
    after = {r["review_id"]: (r["ticket_id"], r["run_id"]) for r in two_runs["v11"]["risk_reviews"]}
    assert all(after[k] == v for k, v in before.items()), "an append must not renumber earlier reviews"


def test_review_ids_deterministic(settings, spec):
    from optpipe.orchestrator import load_inputs, run_session
    inputs = load_inputs(Path(FIXTURE_DIR) / "session_inputs.yaml")
    a, _ = run_session(settings, inputs, inputs.as_of, Path(FIXTURE_DIR))
    b, _ = run_session(settings, inputs, inputs.as_of, Path(FIXTURE_DIR))
    assert [r["review_id"] for r in a["risk_reviews"]] == [r["review_id"] for r in b["risk_reviews"]]


# ---- ruling 3: tick rounding -----------------------------------------------------------------
TR = TickRule.from_list(["SPY", "QQQ", "IWM", "JPM"])


@pytest.mark.parametrize("sym,price,direction,expect,tick", [
    ("JPM", 3.1006, "down", 3.10, 0.05),     # penny pilot >= $3: 0.05
    ("JPM", 3.149, "down", 3.10, 0.05),
    ("JPM", 2.987, "down", 2.98, 0.01),      # penny pilot < $3: 0.01
    ("JPM", 1.512, "up", 1.52, 0.01),        # stop rounds UP
    ("JPM", 6.07, "down", 6.05, 0.05),       # target rounds DOWN
    ("WDC", 2.93, "down", 2.90, 0.05),       # standard < $3: 0.05
    ("WDC", 3.97, "down", 3.90, 0.10),       # standard >= $3: 0.10
    ("WDC", 1.46, "up", 1.50, 0.05),
    ("WDC", 3.04, "down", 3.00, 0.10),
    ("WDC", 3.02, "up", 3.10, 0.10),
])
def test_tick_rounding(sym, price, direction, expect, tick):
    r = TR.round(sym, price, direction)
    assert (r.value, r.tick, r.raw) == (expect, tick, price)


def test_tick_down_across_three_dollars_uses_finer_grid():
    # 3.02 standard rounds down on the 0.10 grid to 3.00 (still >= 3, stays 0.10)
    assert TR.round("WDC", 3.02, "down").value == 3.00
    # 3.00 exact for a penny name stays 3.00
    assert TR.round("JPM", 3.00, "down").value == 3.00


def test_penny_pilot_list_is_configurable():
    assert TickRule.from_list([]).tick("JPM", 2.0) == 0.05
    assert TickRule.from_list(["wdc"]).tick("WDC", 2.0) == 0.01


def test_tickets_store_raw_and_rounded(two_runs):
    for t in two_runs["v11"]["tickets"]:
        lp = t["limit_price"]
        assert lp["rounding"] == "down" and lp["amount"] <= lp["raw_amount"] + 1e-9
        assert round(lp["amount"] / lp["tick_size"], 6) == round(lp["amount"] / lp["tick_size"])
    assert all("raw_amount" not in t["limit_price"] for t in two_runs["v1"]["tickets"]), "v1 stays v1-valid"
    assert all("raw_amount" not in t["limit_price"] for t in to_v1(two_runs["v11"])["tickets"])


# ---- ruling 4: per-run scans -----------------------------------------------------------------
def test_scans_and_snapshots_are_per_run(two_runs):
    v11 = two_runs["v11"]
    runs = [r["run_id"] for r in v11["runs"]]
    assert [s["run_id"] for s in v11["premarket_scans"]] == runs
    assert [s["run_id"] for s in v11["global_snapshots"]] == runs
    assert "premarket_scan" in two_runs["v1"] and "premarket_scans" not in two_runs["v1"]


# ---- ruling 5: both schemas pass ---------------------------------------------------------------
def test_both_schemas_pass(two_runs, validator, spec):
    assert validator.validate(two_runs["v1"]) == []
    assert validator.validate(two_runs["v11"]) == [], "the 1.1.1 record passes the pinned spec"
    v11 = spec.validator_for_schema(PATCH / "session.schema.json")    # the frozen v1.1 proposal only knows 1.1.0
    assert v11.validate({**two_runs["v11"], "schema_version": "1.1.0"}) == []
    assert v11.validate(two_runs["v1"]) == [], "the v1.1 proposal stays backward compatible"
    assert two_runs["v11"]["schema_version"] == "1.1.1" and two_runs["v1"]["schema_version"] == "1.0.0"


def test_open_interest_is_integer_in_output(two_runs):
    for c in two_runs["v1"]["candidates"]:
        for sn in c["snapshots"]:
            for s in sn["option_chain_slices"]:
                for k in s["contracts"]:
                    v = k["open_interest"]["value"]
                    assert v is None or type(v) is int


# ---- ruling 6: closed phase --------------------------------------------------------------------
@pytest.mark.parametrize("ts,phase", [
    ("2026-10-17T10:00:00-04:00", "closed"),       # Saturday
    ("2026-10-18T12:00:00-04:00", "closed"),       # Sunday
    ("2026-11-26T10:00:00-05:00", "closed"),       # Thanksgiving (NYSE holiday)
    ("2026-11-27T12:59:00-05:00", "intraday"),     # early-close day, before 13:00
    ("2026-11-27T13:30:00-05:00", "closed"),       # early-close day, after 13:00
    ("2026-10-13T03:59:00-04:00", "closed"),       # before 04:00
    ("2026-10-13T04:00:00-04:00", "premarket"),
    ("2026-10-13T16:00:00-04:00", "closed"),       # at the close
    ("2026-10-13T22:30:00+02:00", "closed"),       # 16:30 NY seen from Berlin (CEST)
    ("2026-10-26T14:35:00+01:00", "open"),         # DST gap week: 09:35 NY
])
def test_closed_phase(ts, phase, ruleset_calendar):
    from optpipe.orchestrator import phase_for
    assert phase_for(datetime.fromisoformat(ts), ruleset_calendar) == phase


def test_closed_phase_written_as_closed_in_1_1_0(settings, spec, tmp_path):
    from optpipe.orchestrator import load_inputs, run_session
    from optpipe.records.store import write_session
    inputs = load_inputs(Path(FIXTURE_DIR) / "session_inputs.yaml")
    rec, trace = run_session(settings, inputs, datetime.fromisoformat("2026-10-13T17:05:00-04:00"), Path(FIXTURE_DIR))
    assert rec["runs"][0]["phase"] == "closed"
    w = write_session(rec, trace, tmp_path)
    on_disk = json.loads(w.json.read_text())
    assert on_disk["schema_version"] == "1.1.1" and on_disk["runs"][0]["phase"] == "closed"
    errs = spec.load_module("session_validator").validate(on_disk, base_dir=tmp_path)
    assert not [e for e in errs if e.startswith(("INV-10", "INV-11", "INV-12", "INV-13"))], errs
    v1 = to_v1(on_disk)                                    # v1 readers still get afterhours + a note
    assert v1["runs"][0]["phase"] == "afterhours" and "closed" in v1["runs"][0]["summary"]


# ---- Moodle: trace hash ------------------------------------------------------------------------
def test_trace_sha256_matches_sidecar_and_trace_ref(two_runs):
    out = two_runs["out"]
    tp = out / "session-2026-10-13.trace.json"
    sha = hashlib.sha256(tp.read_bytes()).hexdigest()
    assert (out / "session-2026-10-13.trace.json.sha256").read_text().split()[0] == sha
    assert two_runs["v11"]["trace_ref"] == {"path": tp.name, "sha256": sha}
    assert "trace_ref" not in two_runs["v1"], "v1 readers never see the v1.1-only trace_ref"
    tr = json.loads(tp.read_text())
    jp = out / "session-2026-10-13.json"
    # record -> trace: trace file bytes (INV-11); trace -> record: canonical content minus trace_ref (no cycle)
    from optpipe.records.store import SESSION_REF_COVERS, session_content_sha256
    assert tr["session_ref"] == {"path": jp.name, "sha256": session_content_sha256(json.loads(jp.read_text())),
                                 "sha256_covers": SESSION_REF_COVERS, "schema_version": "1.1.1"}


# ---- generated code / patch in sync ------------------------------------------------------------
@pytest.mark.parametrize("script", ["gen_models.py", "make_spec_patch.py"])
def test_generated_files_are_current(script, spec):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--check"], capture_output=True, text=True,
                       cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
