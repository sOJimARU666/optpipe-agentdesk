"""Moodle's FICTIONAL demo scenario: settings block, derived max loss consistent with Doodle's
profiles.demo, and the FICTIONAL DEMO tag on the CLI, Markdown and dashboard."""
import json

import pytest
from pydantic import ValidationError

from optpipe.cli import main as cli_main
from optpipe.config import Settings, load_settings
from optpipe.scenario import TAG, tag_for_record


@pytest.fixture(scope="module")
def demo_out(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    return d


def test_settings_scenario_block():
    s = load_settings()
    sc = s.scenario
    assert (sc.name, sc.repo_name, sc.account_size_usd, sc.fictional) == ("demo", "optpipe-agentdesk", 25000, True)
    assert s.broker.name.endswith("(FICTIONAL)")
    assert s.watchlist == ["JPM", "WDC", "AAPL", "MSFT", "NVDA", "XOM"]
    assert s.sectors == ["Financial", "Technology", "Energy"]


def test_scenario_block_forbids_unknown_keys():
    raw = load_settings().model_dump()
    raw["scenario"]["broker_api_key"] = "x"
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


def test_max_loss_derived_and_consistent_with_doodle_profile(raw_rules):
    """2% of $25,000 = $500 = profiles.demo per_trade_max_loss_usd (= R04 limit) = the old $500 placeholder."""
    s = load_settings()
    sc = s.scenario
    assert sc.max_loss_per_idea_usd == 500
    prof = raw_rules["profiles"][sc.risk_profile]
    assert float(prof["paper_account_equity_usd"]) == sc.account_size_usd
    assert float(prof["max_risk_per_idea_pct"]) == sc.max_risk_per_idea_pct
    assert float(prof["config_overrides"]["per_trade_max_loss_usd"]) == sc.max_loss_per_idea_usd
    assert prof["paper_account_equity_usd"] * prof["max_risk_per_idea_pct"] / 100 == sc.max_loss_per_idea_usd
    assert prof["watchlist"] == s.watchlist
    smap = prof["config_overrides"]["sector_map"]
    assert set(smap) == set(s.watchlist) and set(smap.values()) == set(s.sectors)


def test_planner_uses_derived_max_loss(rec):
    for t in rec["tickets"]:
        assert t["risk_basis"].startswith("Per-trade max loss $500 = 2% of the FICTIONAL $25,000 paper account")
        assert t["max_loss"]["amount_usd"] <= 500


def test_reviews_use_demo_profile(rec, raw_rules):
    for rv in rec["risk_reviews"]:
        assert rv["config_overrides"] == raw_rules["profiles"]["demo"]["config_overrides"]
        ids = [r["rule_id"] for r in rv["results"]]
        assert ids[-2:] == ["R12_DEFINED_RISK_STRUCTURE", "R13_HOLD_PERIOD"]


def test_override_mismatch_sizes_conservatively(settings):
    from optpipe.orchestrator import scenario_max_loss
    from optpipe.risk import load_ruleset
    from optpipe.spec import discover
    rs = load_ruleset(discover(settings.spec_path))[0].with_profile("demo")
    assert scenario_max_loss(settings, rs) == (500, None)
    v, note = scenario_max_loss(settings, rs.with_overrides({"per_trade_max_loss_usd": 300}))
    assert v == 300 and note.startswith("WARNING scenario max loss $500")


def test_tag_in_record_markdown_and_cli(pipeline_output, capsys, tmp_path):
    assert tag_for_record(pipeline_output["record"]) == TAG
    md = (pipeline_output["out"] / "session-2026-10-13.md").read_text()
    assert md.startswith(f"# {TAG} · Paper options session")
    assert cli_main(["run", "--out", str(tmp_path)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith(f"PAPER ONLY | {TAG} | sess-2026-10-13")


def test_no_tag_without_scenario(example):
    assert tag_for_record(example) is None
    from optpipe.records.markdown import render_markdown
    assert TAG not in render_markdown(example).splitlines()[0]


def test_dashboard_banner_has_tag(pipeline_output):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from optpipe.dashboard import create_app
    c = TestClient(create_app(pipeline_output["out"]))
    for url in ("/", "/history", "/s/2026-10-13/tickets"):
        html = c.get(url).text
        assert "PAPER ONLY" in html and f'<b class="demo">{TAG}</b>' in html, url


def test_repo_name_is_only_a_proposal():
    """No code path creates or pushes to a repository: repo_name is display text only."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src"
    used = [p for p in src.rglob("*.py") if "repo_name" in p.read_text()]
    assert sorted(p.name for p in used) == ["config.py", "scenario.py"]
    for p in src.rglob("*.py"):
        t = p.read_text()
        assert "git push" not in t and "api.github.com" not in t, p
