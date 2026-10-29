"""Priorities 3-5: end-to-end offline run from fixtures -> schema-v1 JSON that passes Doodle's
validate_session.py, + Markdown + hand-off trace; planner values; idea log; multi-run sessions."""
import json
from datetime import datetime

import pytest

from optpipe.models.contracts import HYPOTHETICAL_LABEL
from optpipe.records.idea_log import quality_index

FIXTURE_DIR = "tests/fixtures/2026-10-13"


def test_cli_run_writes_json_md_trace(pipeline_output):
    out = pipeline_output["out"]
    assert pipeline_output["rc"] == 0
    for name in ("session-2026-10-13.json", "session-2026-10-13.md", "session-2026-10-13.trace.json"):
        assert (out / name).stat().st_size > 0
    md = (out / "session-2026-10-13.md").read_text()
    assert "PAPER ONLY" in md and "Hand-off trace" in md


def test_output_passes_doodle_validator(rec, validator):
    assert validator.validate(rec) == []


def test_validator_bites_on_broken_output(rec, validator):
    rec["tickets"][0]["candidate_id"] = "cand-nope"
    assert validator.validate(rec) != []


def test_session_is_paper_and_needs_approval(rec):
    assert rec["session"]["mode"] == "paper"
    assert rec["approvals"] == [] and rec["paper_positions"] == []
    assert all(t["label"] == HYPOTHETICAL_LABEL for t in rec["tickets"])
    assert all(r["overall"]["approval_required"] is True for r in rec["risk_reviews"])


def test_trace_has_five_stages_in_order(pipeline_output):
    tr = json.loads((pipeline_output["out"] / "session-2026-10-13.trace.json").read_text())
    steps = tr["runs"][0]["steps"]
    assert [s["stage"] for s in steps] == ["discovery", "research", "planner", "risk_officer", "watcher"]
    assert all(s["error"] is None for s in steps)
    assert tr["runs"][0]["validation"]["errors"] == []


def test_ny_berlin_pairs_and_ny_market_logic(rec):
    s = rec["session"]
    assert s["market_calendar"]["timezone"] == "America/New_York"
    assert s["market_calendar"]["ny_berlin_offset_hours"] == 6          # 2026-10-13: EDT vs CEST
    started = rec["runs"][0]["started"]
    assert datetime.fromisoformat(started["ny"]) == datetime.fromisoformat(started["berlin"])
    assert started["ny"].endswith("-04:00") and started["berlin"].endswith("+02:00")
    assert rec["runs"][0]["phase"] == "premarket"


def test_jpm_bull_call_spread_matches_doodle_example(rec, example):
    mine = next(t for t in rec["tickets"] if t["ticket_id"] == "tkt-jpm-bcs-340-350")
    ex = next(t for t in example["tickets"] if t["ticket_id"] == "tkt-jpm-bcs-340-350")
    assert mine["strategy"] == ex["strategy"]
    assert [l["contract_id"] for l in mine["legs"]] == [l["contract_id"] for l in ex["legs"]]
    assert mine["quantity"] == ex["quantity"] == 1
    assert mine["limit_price"]["amount"] == ex["limit_price"]["amount"] == 3.10
    assert mine["max_loss"]["amount_usd"] == ex["max_loss"]["amount_usd"] == 310
    assert mine["pricing"]["reference_mid"] == ex["pricing"]["reference_mid"] == 3.025


def test_jpm_premarket_review_matches_doodle_example(rec, example):
    mine = next(r for r in rec["risk_reviews"] if r["ticket_id"] == "tkt-jpm-bcs-340-350")
    ex = next(r for r in example["risk_reviews"]
              if r["ticket_id"] == "tkt-jpm-bcs-340-350" and r["run_id"] == "run-premarket")
    # One deliberate difference: Doodle's example has R08 UNKNOWN because no FOMC/NFP calendar was known
    # (and the CPI page was opened at 09:09, after the run). With the macro-calendar sources (replay
    # snapshot fetched 07:50) every macro coverage reaches past the window, and CPI 10-14 / PPI 10-15
    # fall inside it -> FLAG.
    expected = [(r["rule_id"], "FLAG" if r["rule_id"] == "R08_MACRO_EVENTS" else r["result"]) for r in ex["results"]]
    assert dict(expected)["R08_MACRO_EVENTS"] == "FLAG" and \
        next(r for r in ex["results"] if r["rule_id"] == "R08_MACRO_EVENTS")["result"] == "UNKNOWN"
    assert [(r["rule_id"], r["result"]) for r in mine["results"]] == expected
    assert mine["overall"]["verdict"] == ex["overall"]["verdict"] == "VETO"
    for a, b in zip(mine["results"], ex["results"]):
        assert a["threshold"] == b["threshold"], a["rule_id"]


def test_point_in_time_source_after_run_is_invisible(rec):
    """src-jpm-ir-dividend was fetched 09:09 ET, after the 07:52 run -> R09 cannot use it -> UNKNOWN."""
    r = next(r for r in rec["risk_reviews"] if r["ticket_id"] == "tkt-jpm-bcs-340-350")
    assert next(x for x in r["results"] if x["rule_id"] == "R09_EARLY_ASSIGNMENT")["result"] == "UNKNOWN"


def test_all_vetoed_means_no_trade(rec):
    assert rec["tickets"], "fixtures should produce tickets"
    assert {r["overall"]["verdict"] for r in rec["risk_reviews"]} == {"VETO"}
    log = {i["idea_id"]: i for i in rec["idea_log"]}
    assert log["idea-no-trade"]["current_status"] == "approved_pending"
    for t in rec["tickets"]:
        idea = next(i for i in rec["idea_log"] if i.get("ticket_id") == t["ticket_id"])
        assert idea["current_status"] == "vetoed" and idea["history"][-1]["actor"] == "risk_officer"
    assert rec["watcher_rules"] == []        # watcher only defines rules for non-vetoed tickets


def test_conflicting_source_is_null_with_alternatives(rec):
    assert rec["data_quality"]["conflicting"]
    for path in rec["data_quality"]["conflicting"]:
        node = rec
        for p in path.strip("/").split("/"):
            node = node[int(p)] if isinstance(node, list) else node[p]
        assert node["value"] is None and len(node["conflicts"]) >= 2


def test_data_quality_index_is_regenerable(rec):
    q = quality_index(rec)
    for k in ("missing", "stale", "conflicting"):
        assert sorted(rec["data_quality"][k]) == sorted(q[k])


def test_second_run_appends_to_same_session(tmp_path, validator):
    from optpipe.cli import main
    assert main(["run", "--fixtures", FIXTURE_DIR, "--out", str(tmp_path)]) == 0
    assert main(["run", "--fixtures", FIXTURE_DIR, "--out", str(tmp_path), "--append",
                 "--as-of", "2026-10-13T09:40:00-04:00"]) == 0
    rec = json.loads((tmp_path / "session-2026-10-13.json").read_text())
    assert [r["phase"] for r in rec["runs"]] == ["premarket", "open"]
    assert len({r["run_id"] for r in rec["runs"]}) == 2
    assert all(len(c["snapshots"]) == 2 for c in rec["candidates"])
    assert validator.validate(rec) == []
    tr = json.loads((tmp_path / "session-2026-10-13.trace.json").read_text())
    assert len(tr["runs"]) == 2


def test_risk_overrides_recorded_in_reviews(settings, spec):
    from optpipe.orchestrator import load_inputs, run_session
    from pathlib import Path
    s = settings.model_copy(update={"risk_overrides": {"per_trade_max_loss_usd": 300}})
    inputs = load_inputs(Path(FIXTURE_DIR) / "session_inputs.yaml")
    rec, _ = run_session(s, inputs, inputs.as_of, Path(FIXTURE_DIR))
    assert all(r["config_overrides"] == {"per_trade_max_loss_usd": 300} for r in rec["risk_reviews"])
    assert all(t["max_loss"]["amount_usd"] <= 300 for t in rec["tickets"])


@pytest.mark.parametrize("ny,phase", [("06:00", "premarket"), ("08:45", "preopen"), ("09:30", "open"),
                                      ("09:50", "intraday"), ("16:30", "closed")])
def test_phase_from_ny_clock(ny, phase, ruleset_calendar):
    from optpipe.orchestrator import phase_for
    assert phase_for(datetime.fromisoformat(f"2026-10-13T{ny}:00-04:00"), ruleset_calendar) == phase


def test_phase_same_instant_from_berlin(ruleset_calendar):
    from optpipe.orchestrator import phase_for
    # 15:40 CEST Berlin == 09:40 New York -> "open"; the Berlin clock never drives market logic
    assert phase_for(datetime.fromisoformat("2026-10-13T15:40:00+02:00"), ruleset_calendar) == "open"


