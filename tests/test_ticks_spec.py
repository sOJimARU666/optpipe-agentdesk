"""Tick rounding from Doodle's spec (risk_rules.yaml config.tick_rules, 1.1.0) + the credit-spread ruling for 1.1.1.

Debit (spec 1.1.0): limit DOWN, stop UP, target DOWN.  Credit (ruling for 1.1.1, mirrored now): min-credit limit UP,
stop = debit to close DOWN, target = buy-back UP. The penny-pilot list is a PLACEHOLDER read from the spec."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from optpipe.planner.ticks import CREDIT_DIRECTIONS, DEBIT_DIRECTIONS, TickRule

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "2026-10-13"


@pytest.fixture(scope="module")
def spec_ticks(spec):
    return spec.risk_rules()["config"]["tick_rules"]


@pytest.fixture(scope="module")
def vs(spec):
    return spec.load_module("session_validator")


@pytest.fixture(scope="module")
def tr(spec_ticks):
    return TickRule.from_spec(spec_ticks, fallback_penny=["NOPE"], fallback_threshold=99)


def test_spec_has_tick_rules_and_they_win(tr, spec_ticks):
    assert tr.source.startswith("spec ")
    assert tr.penny_pilot == frozenset(s.upper() for s in spec_ticks["penny_pilot"])     # not the fallback
    assert Decimal(str(tr.threshold)) == Decimal(spec_ticks["threshold"])
    assert tr.penny_ticks == tuple(float(x) for x in spec_ticks["penny_ticks"])
    assert tr.standard_ticks == tuple(float(x) for x in spec_ticks["standard_ticks"])
    d = spec_ticks["directions"]
    assert (tr.direction("debit", "limit"), tr.direction("debit", "stop"), tr.direction("debit", "target")) == (
        d["debit_limit"], d["stop"], d["target"]) == ("down", "up", "down")


def test_penny_list_comes_from_spec_not_settings(spec_ticks):
    custom = {**spec_ticks, "penny_pilot": ["WDC"]}
    t = TickRule.from_spec(custom, fallback_penny=["SPY", "QQQ", "IWM", "JPM"])
    assert t.tick("WDC", 2.0) == 0.01 and t.tick("JPM", 2.0) == 0.05


def test_fallback_when_spec_has_no_tick_rules():
    t = TickRule.from_spec(None, fallback_penny=["jpm"], fallback_threshold=3.0)
    assert t.source.startswith("settings") and t.penny_pilot == {"JPM"}
    assert t.directions == {"debit": DEBIT_DIRECTIONS, "credit": CREDIT_DIRECTIONS}


def test_pipeline_rounding_cites_spec_source(record):
    notes = json.dumps(record)
    assert "tick rounding [spec risk_rules.yaml config.tick_rules]" in notes


# ---- credit spreads: Doodle's ruling for spec 1.1.1, mirrored now --------------------------------
def test_credit_direction_map(tr):
    assert {r: tr.direction("credit", r) for r in ("limit", "stop", "target")} == {
        "limit": "up", "stop": "down", "target": "up"}


@pytest.mark.parametrize("sym,raw,role,value,tick", [
    # penny-pilot JPM below $3: 0.01 grid
    ("JPM", 1.2345, "limit", 1.24, 0.01),      # min credit rounds UP: collect no less
    ("JPM", 2.469, "stop", 2.46, 0.01),        # debit to close rounds DOWN: exit no later
    ("JPM", 0.617, "target", 0.62, 0.01),      # buy-back rounds UP: take profit no later
    ("JPM", 1.24, "limit", 1.24, 0.01),        # on-grid values are unchanged
    # penny-pilot at/above $3: 0.05
    ("JPM", 3.1006, "limit", 3.15, 0.05),
    ("JPM", 3.1006, "stop", 3.10, 0.05),
    # standard names: 0.05 below $3, 0.10 at/above
    ("WDC", 2.93, "target", 2.95, 0.05),
    ("WDC", 3.07, "limit", 3.10, 0.10),
    ("WDC", 3.07, "stop", 3.00, 0.10),
    ("WDC", 0.01, "stop", 0.00, 0.05),         # can round to zero: the planner must reject such tickets
])
def test_credit_rounding_vectors(tr, sym, raw, role, value, tick):
    r = tr.round_for(sym, raw, "credit", role)
    assert (r.value, r.tick, r.raw) == (value, tick, raw)
    assert r.direction == CREDIT_DIRECTIONS[role]


def test_credit_is_never_worse_than_raw(tr):
    for raw in [x / 1000 for x in range(1, 8000, 37)]:
        lim, stop, tgt = (tr.round_for("JPM", raw, "credit", k).value for k in ("limit", "stop", "target"))
        assert lim >= raw - 1e-9 and stop <= raw + 1e-9 and tgt >= raw - 1e-9


# ---- parity with Doodle's validator (tick_for / round_tick) on a price grid ---------------------
@pytest.mark.parametrize("sym", ["JPM", "SPY", "WDC", "AAPL"])
def test_parity_with_validator_round_tick(tr, vs, sym):
    for raw in [x / 1000 for x in range(1, 12000, 13)] + [2.999, 3.0, 3.0001, 3.1006, 1.5125, 6.05]:
        for d in ("down", "up", "nearest"):
            r = tr.round(sym, raw, d)
            assert Decimal(repr(r.tick)) == vs.tick_for(sym, raw), (sym, raw)
            assert Decimal(repr(r.value)) == vs.round_tick(raw, vs.tick_for(sym, raw), d), (sym, raw, d)


def test_rounding_down_across_three_dollars_stays_on_coarse_grid(tr):
    # the threshold is on the coarse grid, so a down-round from >= $3 can never need the finer tick
    for raw in (3.0, 3.001, 3.04, 3.09):
        assert tr.round("WDC", raw, "down").value == 3.0 and tr.round("WDC", raw, "down").tick == 0.10


# ---- the example ticket: JPM bull call 340/350 (stop 1.52 / target 6.05) ------------------------
@pytest.fixture(scope="module")
def record(settings):
    from optpipe.orchestrator import load_inputs, run_session
    inputs = load_inputs(FIXTURE_DIR / "session_inputs.yaml")
    rec, _ = run_session(settings, inputs, inputs.as_of, FIXTURE_DIR)
    return rec


def test_jpm_example_stop_and_target(record):
    t = next(t for t in record["tickets"] if t["ticket_id"] == "tkt-jpm-bcs-340-350")
    lp = t["limit_price"]
    assert (lp["amount"], lp["tick_size"], lp["rounding"]) == (3.10, 0.05, "down")
    stop = next(c for c in t["stop_rules"][0]["conditions"] if "raw_value" in c)
    tgt = next(c for c in t["target_rules"][0]["conditions"] if "raw_value" in c)
    assert (stop["value"], stop["tick_size"], stop["rounding"]) == (1.52, 0.01, "up")     # penny-pilot < $3
    assert (tgt["value"], tgt["tick_size"], tgt["rounding"]) == (6.05, 0.05, "down")
    assert stop["raw_value"] == pytest.approx(1.5125) and tgt["raw_value"] == pytest.approx(6.05)


def test_planner_ticks_pass_inv13(record, vs):
    errs = [e for e in vs.validate(copy.deepcopy(record)) if e.startswith("INV-13")]
    assert errs == []


def test_known_gap_credit_stop_vs_spec_1_1_0(record, vs, spec):
    """Spec 1.1.0's INV-13 needs stop 'up' for every ticket; Doodle's ruling makes a credit stop round DOWN.
    While VERSION is 1.1.0 the validator flags it (documented gap). When 1.1.1 lands this test flips to
    requiring the credit stop to pass, so the mirror gets re-checked against the real spec."""
    rec = copy.deepcopy(record)
    t = rec["tickets"][0]
    t["limit_price"]["price_type"] = "credit"
    c = next(c for c in t["stop_rules"][0]["conditions"] if "raw_value" in c)
    r = TickRule.from_spec(spec.risk_rules()["config"]["tick_rules"]).round_for(t["underlying"], c["raw_value"],
                                                                               "credit", "stop")
    c.update(value=r.value, tick_size=r.tick, rounding=r.direction)
    flagged = [e for e in vs.validate(rec) if e.startswith("INV-13") and ".stop_rules." in e and "must be 'up'" in e]
    version = (Path(spec.root) / "VERSION").read_text().strip()
    if version == "1.1.0":
        assert flagged, "spec 1.1.0 should still flag a credit stop rounded down (known gap until 1.1.1)"
    else:
        assert not flagged, f"spec {version}: credit stops rounding DOWN should now be accepted"
