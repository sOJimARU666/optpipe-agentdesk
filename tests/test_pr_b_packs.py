"""FEAT-OODLE-01 PR B: pack wiring, mdfeeds, calendar, Moodle to-dos, --live removed.

Offline: committed fixtures only (OPTIONS_SPEC_DIR=spec). Never reads Oodle's live market-data folder.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from mdpack import DATE, FIX, NOW, SPEC, copy_pack, edit_json, ids, load, rehash
from optpipe import mdfeeds, marketdata as md, nyse_calendar as cal
from optpipe.providers.pack import PackMarket, load_pack_market, pack_macro_from_catalysts
from test_marketdata_r7 import _fresh_contract, _jpm_event_time

NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ Moodle (a)(b)(c)
def test_unverified_vs_conflict_are_distinct(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", lambda x: (x["earnings_time"].update(value="AMC"), _fresh_contract(x)))
    r = load(d)
    assert r.ok
    why = md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",))
    assert ids(why) == {"MD-20-UNVERIFIED"}
    # with a catalyst event_time that contradicts: pack-fatal CONFLICT
    d2 = copy_pack(tmp_path / "c")
    edit_json(d2, "catalysts.json", _jpm_event_time)
    edit_json(d2, "JPM.json", lambda x: x["earnings_time"].update(value="AMC"))
    r2 = load(d2)
    assert not r2.ok and ids(r2.errors) == {"MD-20-CONFLICT"}


def test_ideas_use_only_usable_contracts(tmp_path):
    """Moodle to-do (c): get_chain returns only contracts that pass every CONTRACT_CRITICAL field."""
    d = copy_pack(tmp_path)
    def edit(x):
        _fresh_contract(x)
        # second contract stays STALE
        c2 = dict(x["option_liquidity"]["contracts"][0])
        c2["contract_id"] = "JPM 261030C105"
        c2["strike"] = 105
        for k in md.CONTRACT_CRITICAL:
            c2[k] = dict(c2[k]); c2[k].update(status="STALE", as_of="2026-10-19T16:00:00-04:00")
        x["option_liquidity"]["contracts"].append(c2)
    edit_json(d, "JPM.json", edit)
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    assert r.ok
    usable = md.usable_contracts(r, "JPM", now=NOW)
    assert len(usable) == 1 and usable[0]["strike"] == 102
    m = PackMarket(r, now=NOW, allow_labels=("EXAMPLE",), macro=pack_macro_from_catalysts(r, now=NOW))
    # without hold_end, R07/R08 are skipped; day/calendar/earnings freshness still apply
    ch = m.get_chain("JPM")
    assert len(ch.quotes) == 1 and ch.quotes[0].strike == 102


def test_unknown_earnings_time_is_critical_under_r07():
    r = load(FIX / "valid" / DATE)
    why = md.no_idea_reasons(r, "WDC", now=NOW, allow_labels=("EXAMPLE",))
    assert "MD-20-CRITICAL" in ids(why) and any("earnings_time" in e for e in why)


# ------------------------------------------------------------------ calendar + mdfeeds
def test_nyse_calendar_covers_2026_and_2027_and_pins_2026_to_spec():
    assert cal.COVERS_FROM == date(2026, 1, 1) and cal.COVERS_TO == date(2027, 12, 31)
    assert cal.is_session(date(2026, 7, 3)) is False          # Friday holiday
    assert cal.is_session(date(2027, 7, 5)) is False          # Monday holiday (observed)
    assert cal.is_session(date(2028, 1, 3)) is None           # outside coverage = UNKNOWN
    assert cal.close_at(date(2026, 11, 27)).hour == 13        # early close
    # pin 2026 holidays to the spec risk_rules.yaml
    rules = yaml.safe_load((SPEC / "risk_rules.yaml").read_text())
    mh = rules["config"]["market_hours"]
    assert set(mh["holidays"]) == {d.isoformat() for d in cal.HOLIDAYS if d.year == 2026}
    assert set(mh["early_closes"]) == {d.isoformat() for d in cal.EARLY_CLOSES if d.year == 2026}


def test_mdfeeds_calendar_outside_coverage_is_no_idea():
    r = load(FIX / "valid" / DATE)
    far = datetime(2028, 6, 1, 12, 0, tzinfo=NY)
    why = mdfeeds.calendar_reasons(r, now=far)
    assert any(e.startswith("MD-19-CALENDAR") for e in why)


def test_mdfeeds_r07_r08_against_hold_end(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", _fresh_contract)
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    m = PackMarket(r, now=NOW, allow_labels=("EXAMPLE",), macro=pack_macro_from_catalysts(r, now=NOW))
    # hold through earnings day: OK for R07; hold past it: CRITICAL R07
    hold_ok = cal.close_at(date(2026, 10, 21))
    hold_past = cal.close_at(date(2026, 10, 23))
    assert hold_ok and hold_past
    assert not any("R07" in e for e in m.reasons("JPM", hold_end=hold_ok))
    assert any("R07" in e for e in m.reasons("JPM", hold_end=hold_past))
    # R08: pack has FOMC only -> other kinds missing -> CRITICAL R08
    assert any("R08" in e for e in m.reasons("JPM", hold_end=hold_ok))


def test_select_pack_uses_ny_date_and_injected_clock(tmp_path):
    root = FIX / "valid"
    # now before pack_built_at -> FUTURE-PACK
    early = datetime(2026, 10, 20, 8, 0, tzinfo=NY)
    r = mdfeeds.select_pack(root, early, spec=SPEC)
    assert not r.ok and "MD-19-FUTURE-PACK" in ids(r.errors)
    r2 = mdfeeds.select_pack(root, NOW, spec=SPEC)
    assert r2.ok
    r3 = mdfeeds.select_pack(root, datetime(2026, 10, 20, 9, 5))   # naive: MD-19-TZ, nothing loaded
    assert not r3.ok and "MD-19-TZ" in ids(r3.errors)


# ------------------------------------------------------------------ CLI: --live gone, pack watch / run
def test_run_rejects_live_flag(capsys):
    from optpipe.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["run", "--live", "--out", "/tmp"])
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments: --live" in err


def test_run_help_has_data_dir_flag(capsys):
    from optpipe.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["run", "--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "--data-dir" in out
    assert "\n  --live" not in out and " [--live]" not in out


def test_watch_help_has_data_dir_flag(capsys):
    from optpipe.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["watch", "--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "--data-dir" in out
    assert "\n  --live" not in out and " [--live]" not in out


def test_pack_quote_source_reads_fixture_pack(tmp_path):
    from optpipe.watcher.runtime import PackQuoteSource
    from optpipe.records.fields import occ_symbol
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", _fresh_contract)
    edit_json(d, "catalysts.json", _jpm_event_time)
    src = PackQuoteSource(d.parent, allow_labels=("EXAMPLE",), spec=SPEC)
    # contract_id OCC for the fresh JPM 102 call 2026-10-23
    cid = occ_symbol("JPM", date(2026, 10, 23), "C", 102.0)
    snap = src.snapshot(NOW, ["JPM"], [cid])
    assert snap["underlyings"]["JPM"]["last"]["value"] == 101.25
    assert snap["options"][cid]["bid"]["value"] == 1.1
    assert src.method == "file" and src.calls


def test_record_version_is_1_1_8():
    from optpipe import orchestrator
    from optpipe.records import compat
    assert orchestrator.SCHEMA_VERSION == compat.V11 == "1.1.8"
    assert "1.1.8" in compat.V11_SHAPE and "1.1.7" in compat.V11_SHAPE
