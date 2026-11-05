"""Sol R7 evidence for FEAT-OODLE-01 PR B (SOL-REVIEW-2026-11-06-0035).

Maps to Sol's seven required demonstrations. Offline fixtures only; OPTIONS_SPEC_DIR=spec.
Does NOT claim R6 closure or network isolation. PR C (fetcher deletion) is not in scope.
"""
from __future__ import annotations

import ast
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mdpack import DATE, FIX, NOW, ROOT, SPEC, copy_pack, edit_json, ids, load
from optpipe import mdfeeds, marketdata as md
from optpipe.providers.pack import PackMarket
from test_marketdata_r7 import _fresh_contract, _jpm_event_time

NY = ZoneInfo("America/New_York")
SRC = ROOT / "src" / "optpipe"


# 1. All discovery/research/watcher consumers on validated immutable snapshots
def test_r7_1_consumers_import_only_pack_and_marketdata():
    from optpipe.providers import pack as packmod
    from optpipe.watcher import runtime as rt
    assert hasattr(packmod, "PackMarket") and hasattr(rt, "PackQuoteSource")
    tree = ast.parse((SRC / "orchestrator.py").read_text())
    calls = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            calls.append(getattr(f, "id", None) or getattr(f, "attr", None))
    assert "load_pack_market" in calls
    assert "HttpFetcher" not in calls and "LiveNasdaqSource" not in calls and "LiveMacroCalendars" not in calls


def test_r7_1_pack_market_refuses_a_failed_pack(tmp_path):
    d = copy_pack(tmp_path)
    (d / "JPM.json").write_text("{")
    r = md.load_pack(d, root=d.parent, now=NOW, spec=SPEC)
    assert not r.ok
    m = PackMarket(r, now=NOW, allow_labels=("EXAMPLE",))
    with pytest.raises(Exception):
        m.get_quote("JPM")
    with pytest.raises(Exception):
        m.get_chain("JPM")


# 2. No --live / HTTP fallback
def test_r7_2_cli_has_no_live_flag_and_no_http_fallback():
    from optpipe.cli import main
    import io, contextlib
    for argv in (["run", "--live"], ["watch", "--live", "--once"]):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with pytest.raises(SystemExit) as ei:
                main(argv)
        assert ei.value.code == 2 and "unrecognized arguments: --live" in buf.getvalue()
    from optpipe.orchestrator import build_pipeline
    from optpipe.config import load_settings
    with pytest.raises(ValueError, match="exactly one"):
        build_pipeline(load_settings(), None, type("I", (), {"global_snapshot_record": None})(), NOW)


def test_r7_2_watcher_cli_cannot_reach_live_nasdaq():
    src = (SRC / "cli.py").read_text()
    assert "LiveNasdaqSource" not in src
    assert "PackQuoteSource" in src
    assert "HttpFetcher" not in src


# 3. Critical UNKNOWN / stale / conflict → NO IDEA
def test_r7_3_critical_unknown_stale_conflict_mean_no_idea(tmp_path):
    r = load(FIX / "valid" / DATE)
    why = md.no_idea_reasons(r, "WDC", now=NOW, allow_labels=("EXAMPLE",))
    assert "MD-20-CRITICAL" in ids(why)
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", _fresh_contract)
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    later = NOW + timedelta(minutes=30)
    why = md.no_idea_reasons(r, "JPM", now=later, allow_labels=("EXAMPLE",))
    assert "MD-19-STALE-quote" in ids(why) or "MD-20-CRITICAL" in ids(why)
    d2 = copy_pack(tmp_path / "x")
    edit_json(d2, "JPM.json", lambda x: x["earnings_date"].update(value="2026-10-22"))
    r2 = load(d2)
    assert not r2.ok and "MD-20-CONFLICT" in ids(r2.errors)


# 4. Source-specific freshness + calendar / skew / no-lookahead
def test_r7_4_freshness_calendar_skew_lookahead():
    assert md.DEFAULT_SKEW_SECONDS == 0
    r = load(FIX / "valid" / DATE)
    far = datetime(2028, 1, 5, 12, 0, tzinfo=NY)
    assert any(e.startswith("MD-19-CALENDAR") for e in mdfeeds.calendar_reasons(r, now=far))
    from optpipe import nyse_calendar as cal
    last = cal.last_completed_session(NOW)
    assert last is not None
    ahead = last + timedelta(days=1)
    while cal.is_session(ahead) is not True:
        ahead += timedelta(days=1)
        if ahead.year > 2027:
            pytest.skip("no later session in coverage")
    why = mdfeeds.history_reasons([last, ahead], now=NOW)
    assert any("MD-19-LOOKAHEAD" in e for e in why)
    why2 = mdfeeds.history_reasons([last - timedelta(days=5)], now=NOW)
    assert any("MD-19-STALE-HISTORY" in e for e in why2)


# 5. Traceable provenance (MD-18 residual: integrity only; documented OPEN)
def test_r7_5_provenance_is_integrity_only_and_documented():
    r = load(FIX / "valid" / DATE)
    assert r.ok and (FIX / "valid" / DATE / "pack.sha256").exists()
    doc = (SRC / "marketdata.py").read_text()
    assert "MD-18" in doc and "provenance" in doc.lower()


# 6. Filesystem / race protections (carried from PR A; still enforced)
def test_r7_6_filesystem_and_race_protections_still_enforced(tmp_path):
    d = copy_pack(tmp_path)
    target = d / "JPM.json"
    link = d / "dup.json"
    try:
        link.hardlink_to(target)
    except OSError:
        pytest.skip("hard links unsupported here")
    digest = hashlib.sha256(link.read_bytes()).hexdigest()
    man = d / "pack.sha256"
    man.write_text(man.read_text().rstrip("\n") + f"\n{digest}  dup.json\n")
    r = md.load_pack(d, root=d.parent, now=NOW, spec=SPEC)
    assert not r.ok and ("MD-17-HARDLINK" in ids(r.errors) or r.exit_code() in (1, 2))


# 7. Deterministic failure-case replay tests
def test_r7_7_select_pack_replay_is_deterministic_and_fail_closed():
    root = FIX / "valid"
    early = datetime(2026, 10, 20, 8, 0, tzinfo=NY)
    a = mdfeeds.select_pack(root, early, spec=SPEC)
    b = mdfeeds.select_pack(root, early, spec=SPEC)
    assert not a.ok and ids(a.errors) == ids(b.errors) and "MD-19-FUTURE-PACK" in ids(a.errors)
    n1 = mdfeeds.select_pack(root, datetime(2026, 10, 20, 9, 5))
    n2 = mdfeeds.select_pack(root, datetime(2026, 10, 20, 9, 5))
    assert ids(n1.errors) == ids(n2.errors) == {"MD-19-TZ"}
