"""R08 / R09 use the FULL hold window [max(evaluated_at, hold start), hold end], capped at the close of the
earliest leg expiry (M3 brief). Built on Doodle's risk_rules.yaml fixture (JPM 340/350 call spread,
evaluated 2026-10-14 11:00 NY, hold end 10-27, legs expire 10-30, coverage through 12-31)."""
from __future__ import annotations

import copy

import pytest

from optpipe.risk import evaluate, load_ruleset

CPI = {"event_id": "ev-cpi-nov", "kind": "CPI", "underlying": None, "timing": "at_time",
       "scheduled_at": {"value": "2026-11-10T08:30:00-05:00", "status": "ok", "as_of": "2026-10-14T08:00:00-04:00"}}


@pytest.fixture(scope="module")
def ruleset(spec):
    return load_ruleset(spec)[0]


def _run(raw_rules, ruleset, hold_end, expiries, events=None, rule="R08"):
    inp = copy.deepcopy(raw_rules["fixture"])
    inp["ticket"]["hold_window_end"] = hold_end
    for leg, exp in zip(inp["legs"], expiries):
        leg["expiry"] = exp
    if events is not None:
        inp["events"] = events
    out = evaluate(inp, ruleset).model_dump(mode="json")
    return next(r for r in out["results"] if r["rule_id"].startswith(rule))


def _exdiv(day: str, amount=1.65):
    return {"event_id": f"ev-jpm-exdiv-{day}", "kind": "ex_dividend", "underlying": "JPM", "timing": "unknown",
            "scheduled_at": {"value": f"{day}T00:00:00-04:00" if day < "2026-11-01" else f"{day}T00:00:00-05:00",
                             "status": "ok", "as_of": "2026-10-14T08:00:00-04:00"},
            "dividend_amount": {"value": amount, "status": "ok", "as_of": "2026-10-14T08:00:00-04:00"}}


# ---------------------------------------------------------------- R08
def test_r08_event_weeks_away_inside_full_window_is_flagged(raw_rules, ruleset):
    """Not just the next few days: CPI 11-10 (27 days after evaluation) counts when hold + expiries reach it."""
    r = _run(raw_rules, ruleset, "2026-11-20T16:00:00-05:00", ["2026-11-20", "2026-11-20"], [CPI])
    assert r["result"] == "FLAG" and r["measured"]["events_in_window"] == ["ev-cpi-nov"]


def test_r08_event_after_earliest_expiry_is_not_in_window(raw_rules, ruleset):
    """Hold end 11-20 but legs expire 10-30: window capped at 10-30 16:00, CPI 11-10 falls outside."""
    r = _run(raw_rules, ruleset, "2026-11-20T16:00:00-05:00", ["2026-10-30", "2026-10-30"], [CPI])
    assert r["result"] == "PASS" and r["measured"]["events_in_window"] == []


def test_r08_mixed_expiries_cap_at_the_earliest_leg(raw_rules, ruleset):
    r = _run(raw_rules, ruleset, "2026-11-20T16:00:00-05:00", ["2026-11-20", "2026-10-30"], [CPI])
    assert r["measured"]["events_in_window"] == []
    r2 = _run(raw_rules, ruleset, "2026-11-20T16:00:00-05:00", ["2026-11-20", "2026-11-13"], [CPI])
    assert r2["measured"]["events_in_window"] == ["ev-cpi-nov"]


def test_r08_hold_end_before_expiry_still_caps(raw_rules, ruleset):
    r = _run(raw_rules, ruleset, "2026-11-09T16:00:00-05:00", ["2026-11-20", "2026-11-20"], [CPI])
    assert r["measured"]["events_in_window"] == []


def test_r08_early_close_expiry_caps_at_13_00(raw_rules, ruleset):
    """Expiry on the 11-27 early close: an at_time event at 13:30 that day is AFTER the capped window end."""
    ev = dict(CPI, scheduled_at={**CPI["scheduled_at"], "value": "2026-11-27T13:30:00-05:00"})
    r = _run(raw_rules, ruleset, "2026-12-04T16:00:00-05:00", ["2026-11-27", "2026-11-27"], [ev])
    assert r["measured"]["events_in_window"] == []
    ev2 = dict(CPI, scheduled_at={**CPI["scheduled_at"], "value": "2026-11-27T12:30:00-05:00"})
    r2 = _run(raw_rules, ruleset, "2026-12-04T16:00:00-05:00", ["2026-11-27", "2026-11-27"], [ev2])
    assert r2["measured"]["events_in_window"] == ["ev-cpi-nov"]


# ---------------------------------------------------------------- R09 (short call vs ex-dividend)
def test_r09_exdiv_inside_capped_window_counts(raw_rules, ruleset):
    """Ex-div 10-29: exposure = close of 10-28, before the 10-30 expiry close -> in window (FLAG or VETO)."""
    r = _run(raw_rules, ruleset, "2026-11-20T16:00:00-05:00", ["2026-10-30", "2026-10-30"],
             [_exdiv("2026-10-29")], rule="R09")
    assert r["measured"]["leg-2"]["exdiv_in_window"] == ["ev-jpm-exdiv-2026-10-29"]
    assert r["result"] in ("FLAG", "VETO")


def test_r09_exdiv_after_earliest_expiry_is_ignored(raw_rules, ruleset):
    """Ex-div 11-06 is inside the 11-20 hold end but after the 10-30 expiry close -> not in window."""
    r = _run(raw_rules, ruleset, "2026-11-20T16:00:00-05:00", ["2026-10-30", "2026-10-30"],
             [_exdiv("2026-11-06")], rule="R09")
    assert r["measured"]["leg-2"]["exdiv_in_window"] == [] and r["result"] == "PASS"
    r2 = _run(raw_rules, ruleset, "2026-11-20T16:00:00-05:00", ["2026-11-20", "2026-11-20"],
              [_exdiv("2026-11-06")], rule="R09")
    assert r2["measured"]["leg-2"]["exdiv_in_window"] == ["ev-jpm-exdiv-2026-11-06"]
