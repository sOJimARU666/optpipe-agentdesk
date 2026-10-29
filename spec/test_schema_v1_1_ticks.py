"""pytest: v1.1 per-run premarket_scans/global_snapshots (INV-12) and raw tick fields (INV-13)."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

import validate_session as vs
from test_schema_v1_1 import requality, strip_v1_1

SPEC = Path(__file__).resolve().parent
EXAMPLE = json.loads((SPEC / "session.example.json").read_text())


def ex():
    return copy.deepcopy(EXAMPLE)


def errs(s, prefix):
    return [e for e in vs.validate(s) if e.startswith(prefix)]


def ticket(s):
    return next(t for t in s["tickets"] if "raw_amount" in t["limit_price"])


def cond_in(t, grp):
    return next(c for r in t[grp] for c in r["conditions"] if "raw_value" in c)


# ---------------------------------------------------------------- example carries the new fields
def test_example_has_v1_1_fields_and_is_valid():
    s = ex()
    assert s["premarket_scans"][0]["run_id"] == s["premarket_scan"]["run_id"]
    assert s["global_snapshots"][0]["run_id"] == s["global_snapshot"]["run_id"]
    t = ticket(s)
    assert (t["limit_price"]["amount"], t["limit_price"]["tick_size"], t["limit_price"]["rounding"]) == (3.10, 0.05, "down")
    assert cond_in(t, "stop_rules")["value"] == 1.52 and cond_in(t, "target_rules")["value"] == 6.05
    assert vs.validate(s) == []


def test_no_proposal_wording_left():
    assert "PROPOSAL" not in (SPEC / "session.schema.json").read_text()


# ---------------------------------------------------------------- INV-12
@pytest.mark.parametrize("arr", ["premarket_scans", "global_snapshots"])
def test_inv12_unknown_run(arr):
    s = ex(); x = copy.deepcopy(s[arr][0]); x["run_id"] = "run-nope"; s[arr].append(x)
    assert any("does not reference an existing run" in e for e in errs(s, "INV-12"))


@pytest.mark.parametrize("arr", ["premarket_scans", "global_snapshots"])
def test_inv12_duplicate_run(arr):
    s = ex(); s[arr].append(copy.deepcopy(s[arr][0]))
    assert any("duplicate run_id" in e for e in errs(s, "INV-12"))


def test_inv12_extra_run_ok_and_singular_must_be_listed():
    s = ex(); x = copy.deepcopy(s["premarket_scans"][0]); x["run_id"] = "run-preopen"; s["premarket_scans"].append(x)
    assert vs.validate(requality(s)) == []
    s["premarket_scans"] = [x]
    requality(s)
    assert any("premarket_scan.run_id" in e for e in errs(s, "INV-12"))


def test_inv12_arrays_require_1_1_0():
    s = strip_v1_1(ex()); s["schema_version"] = "1.0.0"
    assert vs.validate(s) == []
    s["global_snapshots"] = [copy.deepcopy(s["global_snapshot"])]
    requality(s)
    assert errs(s, "INV-12") == ["INV-12 global_snapshots requires schema_version 1.1.0, 1.1.1 or 1.1.2"]


def test_inv12_items_are_schema_checked():
    s = ex(); s["premarket_scans"][0]["bogus"] = 1
    assert any(e.startswith("schema premarket_scans/0") for e in vs.validate(s))


# ---------------------------------------------------------------- tick rule vectors (mirrors options-pipeline ticks tests)
@pytest.mark.parametrize("und,raw,direction,out,tick", [
    ("JPM", 3.1006, "down", "3.10", "0.05"), ("JPM", 3.149, "down", "3.10", "0.05"), ("JPM", 2.987, "down", "2.98", "0.01"),
    ("JPM", 1.512, "up", "1.52", "0.01"), ("JPM", 6.07, "down", "6.05", "0.05"), ("WDC", 2.93, "down", "2.90", "0.05"),
    ("WDC", 3.97, "down", "3.90", "0.10"), ("WDC", 1.46, "up", "1.50", "0.05"), ("WDC", 3.04, "down", "3.00", "0.10"),
    ("WDC", 3.02, "up", "3.10", "0.10"), ("JPM", 3.1006249999999995, "down", "3.10", "0.05"), ("SPY", 2.995, "nearest", "3.00", "0.01"),
    ("WDC", 3.0, "down", "3.00", "0.10"), ("JPM", 2.999, "up", "3.00", "0.01"),
])
def test_tick_vectors(und, raw, direction, out, tick):
    assert vs.tick_for(und, raw) == Decimal(tick)
    assert vs.round_tick(raw, vs.tick_for(und, raw), direction) == Decimal(out)


# ---------------------------------------------------------------- INV-13 on the example ticket
def test_inv13_wrong_rounded_limit():
    s = ex(); t = ticket(s); t["limit_price"]["amount"] = 3.05
    for a in s["approvals"]:
        a["limit_price"] = copy.deepcopy(t["limit_price"])
    assert any("rounded 3.05" in e for e in errs(s, "INV-13"))


def test_inv13_debit_limit_must_round_down():
    s = ex(); t = ticket(s); t["limit_price"].update(rounding="up", amount=3.15)
    for a in s["approvals"]:
        a["limit_price"] = copy.deepcopy(t["limit_price"])
    assert any("must be 'down'" in e for e in errs(s, "INV-13"))


def test_inv13_wrong_tick_for_penny_name():
    s = ex(); t = ticket(s); c = cond_in(t, "stop_rules"); c.update(tick_size=0.05, value=1.55)
    assert any("tick_size 0.05 != 0.01" in e for e in errs(s, "INV-13"))


def test_inv13_standard_name_tick():
    s = ex(); t = ticket(s); t["underlying"] = "WDC"   # standard: 0.10 at/above $3, 0.05 below
    e = errs(s, "INV-13")
    assert any("limit_price tick_size 0.05 != 0.10" in x for x in e) and any("stop_rules" in x and "0.01 != 0.05" in x for x in e)


def test_inv13_stop_must_round_up_target_down():
    s = ex(); t = ticket(s)
    cond_in(t, "stop_rules").update(rounding="down", value=1.51)
    cond_in(t, "target_rules").update(rounding="up", value=6.05)
    e = errs(s, "INV-13")
    assert any("stop_rules" in x and "must be 'up'" in x for x in e) and any("target_rules" in x and "must be 'down'" in x for x in e)


def test_inv13_watcher_condition_consistency():
    s = ex(); w = next(w for w in s["watcher_rules"] if any("raw_value" in c for c in w["conditions"]))
    c = next(c for c in w["conditions"] if "raw_value" in c); c["value"] = round(c["value"] + 0.01, 2)
    assert any(x.startswith(f"INV-13 watch {w['watch_id']}") for x in errs(s, "INV-13"))


def test_inv13_rounding_none_must_equal_raw():
    s = ex(); c = cond_in(ticket(s), "entry_rules"); c.update(rounding="none", raw_value=3.1, value=3.1)
    assert errs(s, "INV-13") == []
    c["value"] = 3.05
    assert errs(s, "INV-13")


def test_inv13_fields_are_all_or_nothing():
    s = ex(); del ticket(s)["limit_price"]["tick_size"]
    assert any(e.startswith("schema tickets") and "tick_size" in e for e in vs.validate(s))


def test_inv13_raw_fields_require_1_1_0():
    s = strip_v1_1(ex()); s["schema_version"] = "1.0.0"
    assert vs.validate(s) == []
    ticket_ = s["tickets"][0]
    ticket_["limit_price"].update(raw_amount=3.100625, tick_size=0.05, rounding="down")
    for a in s["approvals"]:
        if a["ticket_id"] == ticket_["ticket_id"]:
            a["limit_price"] = copy.deepcopy(ticket_["limit_price"])
    assert any("require schema_version 1.1.0" in e for e in errs(s, "INV-13"))


# ---------------------------------------------------------------- pipeline-shaped 1.1.0 preview (shape of options-pipeline record)
def test_pipeline_shaped_values_with_float_noise():
    s = ex(); t = ticket(s)
    t["limit_price"]["raw_amount"] = 3.025 * 1.025          # 3.1006249999999995 as the pipeline writes it
    for a in s["approvals"]:
        a["limit_price"] = copy.deepcopy(t["limit_price"])
    assert vs.validate(s) == []
