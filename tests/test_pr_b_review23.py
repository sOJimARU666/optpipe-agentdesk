"""Moodle PR #23 review regressions (FEAT-OODLE-01 PR B).

One test per required finding, plus the in-scope freshness / calendar / IV-history holes.
Offline fixtures only. Does not close MD-18 or R6, and does not delete the HTTP fetchers.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mdpack import DATE, FIX, NOW, SPEC, copy_pack, edit_json, ids, load
from optpipe import mdfeeds, marketdata as md
from optpipe.config import load_settings
from optpipe.models.common import ScreenResult, ScreenRow
from optpipe.models.contracts import DiscoveryInput
from optpipe.providers.pack import PackMarket, pack_macro_from_catalysts
from optpipe.research.agent import ResearchAgent
from test_marketdata_r7 import _fresh_contract, _jpm_event_time

NY = ZoneInfo("America/New_York")


def _passing_jpm(tmp_path, *, earnings_status="OK"):
    """EXAMPLE JPM with a fresh contract and a catalyst event_time that corroborates BMO."""
    d = copy_pack(tmp_path)

    def edit(x, status=earnings_status):
        _fresh_contract(x)
        x["earnings_time"].update(status=status, value="BMO")

    edit_json(d, "JPM.json", edit)
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    assert r.ok, r.errors
    return d, r


def _add_macro_releases(doc):
    """Schema-valid macro_release rows. Kind is the note token (CPI / PPI / NFP)."""
    src = {"source": "example_calendar", "url": "https://calendar.example.org/macro",
           "as_of": "2026-10-20T08:30:00-04:00", "origin": "data_vendor", "origin_entity": "Example Calendar Co"}
    for kind, ed in (("CPI", "2026-11-10"), ("PPI", "2026-11-13"), ("NFP", "2026-11-06")):
        doc["catalysts"].append({
            "ticker": None, "catalyst_type": "macro_release", "event_date": ed,
            "event_time": f"{ed}T08:30:00-05:00", "status": "UNCONFIRMED",
            "sources": [dict(src)], "note": kind,
        })


# -------------------------------------------------------------------------- 1
def test_run_data_dir_research_completes_and_writes_session(tmp_path):
    """optpipe run --data-dir must finish research and write a session (no get_calendars crash)."""
    from optpipe.cli import main
    d = copy_pack(tmp_path)
    out = tmp_path / "out"
    rc = main(["run", "--data-dir", str(d.parent), "--as-of", NOW.isoformat(),
               "--allow-example", "--out", str(out)])
    sessions = [p for p in out.glob("session-*.json") if ".trace." not in p.name]
    assert sessions, "no session file written"
    rec = json.loads(sessions[0].read_text())
    assert rec["schema_version"] == "1.1.8"
    traces = list(out.glob("*.trace.json"))
    assert traces, "no trace written"
    body = traces[0].read_text()
    assert "stage research failed" not in body
    research = [s for s in json.loads(body)["runs"][-1]["steps"] if s["stage"] == "research"]
    assert research and research[0]["error"] is None
    assert rc == 0


# -------------------------------------------------------------------------- 2
def test_stale_bmo_earnings_time_is_no_idea(tmp_path):
    """A STALE BMO/AMC is not corroboration. It is NO IDEA (MD-20-CRITICAL), even when a catalyst matches."""
    d, r = _passing_jpm(tmp_path, earnings_status="STALE")
    why = md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",))
    dec = mdfeeds.decision_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",))
    assert ids(why) == {"MD-20-CRITICAL"}
    assert any("earnings_time" in e and "STALE" in e for e in why)
    assert any("earnings_time" in e and "STALE" in e for e in dec)
    assert not any("MD-20-UNVERIFIED" in e for e in why + dec)
    # the OK twin is not this hole
    d2 = copy_pack(tmp_path / "ok")
    edit_json(d2, "JPM.json", _fresh_contract)
    edit_json(d2, "catalysts.json", _jpm_event_time)
    ok = load(d2)
    assert md.no_idea_reasons(ok, "JPM", now=NOW, allow_labels=("EXAMPLE",)) == []


# -------------------------------------------------------------------------- 3
def test_no_idea_ticker_does_not_move_watcher_and_pack_quote_keeps_iv(tmp_path):
    """Watcher quotes fail closed on NO IDEA. A passing chain carries the pack IV. Fresh pack quotes are not stale."""
    from optpipe.records.fields import occ_symbol
    from optpipe.watcher.engine import _Poll, w02
    from optpipe.watcher.runtime import PackQuoteSource

    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", lambda x: x["earnings_time"].update(value=None, status="UNKNOWN"))
    src = PackQuoteSource(d.parent, allow_labels=("EXAMPLE",), spec=SPEC)
    cid = occ_symbol("JPM", date(2026, 10, 23), "C", 102.0)
    snap = src.snapshot(NOW, ["JPM"], [cid])
    last = snap["underlyings"]["JPM"]["last"]
    assert last["value"] is None and last["status"] == "missing"
    blocked = {"now": NOW.isoformat(), "target": {"id": "t1"},
               "underlying": {"last": last, "prev_last": 200.0,
                              "levels": {"stop_below": 150.0}, "bars_5m": []},
               "legs": []}
    assert w02(_Poll(blocked, {}, None)) is None
    would = {"now": NOW.isoformat(), "target": {"id": "t1"},
             "underlying": {"last": {"value": 101.25, "status": "ok", "as_of": NOW.isoformat()},
                            "prev_last": 200.0, "levels": {"stop_below": 150.0}, "bars_5m": []},
             "legs": []}
    assert w02(_Poll(would, {}, None)) is not None

    d2, _stale_iv = _passing_jpm(tmp_path / "pass")

    def ok_iv(x):
        _fresh_contract(x)
        x["iv"].update(status="OK", value=31.5, as_of="2026-10-20T08:55:00-04:00")

    edit_json(d2, "JPM.json", ok_iv)
    r = load(d2)
    assert r.ok, r.errors
    m = PackMarket(r, now=NOW, allow_labels=("EXAMPLE",), macro=pack_macro_from_catalysts(r, now=NOW))
    q = m.get_quote("JPM")
    assert q.is_realtime is True and q.last == 101.25
    assert ResearchAgent._quote(q, "src-pack", q.fetched_at)[0]["last"]["status"] == "ok"
    ch = m.get_chain("JPM")
    assert ch.quotes and ch.quotes[0].bid == 1.1 and ch.quotes[0].iv == 31.5
    assert all(oq.iv == 31.5 for oq in ch.quotes)


# -------------------------------------------------------------------------- 4
def test_r07_r08_gate_the_idea_path(tmp_path):
    """Discovery/research on a pack supply a hold end, so FOMC-only R08 (and R07) actually drop ideas."""
    from optpipe.orchestrator import Orchestrator, SessionInputs, build_pipeline

    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", _fresh_contract)
    edit_json(d, "catalysts.json", _jpm_event_time)
    settings = load_settings()
    pipe = build_pipeline(settings, None, SessionInputs(), NOW, data_dir=d.parent,
                          allow_labels=("MARKET", "EXAMPLE", "TEST"))
    orch = Orchestrator(pipe)
    assert orch.pack.hold_end is None
    _rec, _trace, notes = orch.run(SessionInputs(), NOW)
    assert orch.pack.hold_end is not None
    gated = orch.pack.reasons("JPM")
    assert any("R07" in e for e in gated) and any("R08" in e for e in gated)
    assert any("NO IDEA JPM" in n for n in notes)
    assert all(c.get("underlying") != "JPM" for c in _rec["candidates"])
    # helper still skips R07/R08 when the caller passes no hold end
    m = PackMarket(load(d), now=NOW, allow_labels=("EXAMPLE",),
                   macro=pack_macro_from_catalysts(load(d), now=NOW))
    assert not any("R08" in e for e in m.reasons("JPM", hold_end=None))
    assert any("R08" in e for e in m.reasons("JPM", hold_end=orch.pack.hold_end))
    # BMO vs AMC: a hold through the earnings session includes a BMO print and not an AMC print
    from optpipe import nyse_calendar as cal
    before = cal.close_at(date(2026, 10, 20))
    on_day = cal.close_at(date(2026, 10, 21))
    assert not any("R07" in e for e in m.reasons("JPM", hold_end=before))
    assert any("R07" in e and "BMO" in e for e in m.reasons("JPM", hold_end=on_day))


def test_macro_age_comes_from_source_as_of_not_pack_built_at(tmp_path):
    d = copy_pack(tmp_path)

    def old_fomc(doc):
        for c in doc["catalysts"]:
            if c.get("catalyst_type") == "fomc":
                for s in c["sources"]:
                    s["as_of"] = "2026-01-02T09:00:00-05:00"

    edit_json(d, "catalysts.json", old_fomc)
    r = load(d)
    assert r.ok, r.errors
    snaps = pack_macro_from_catalysts(r, now=NOW)
    fomc = next(s for s in snaps if s.kind == "FOMC")
    assert fomc.fetched_at.year == 2026 and fomc.fetched_at.month == 1
    assert fomc.fetched_at != mdfeeds.built_at(r)
    _end, why = mdfeeds.macro_coverage(snaps, now=NOW, built=mdfeeds.built_at(r))
    assert any("MD-19-STALE-MACRO" in e and "FOMC" in e for e in why)


def test_prior_close_must_be_the_official_close(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "catalysts.json",
              lambda doc: doc["ticker_context"][0]["prior_close"].update(as_of="2026-10-19T10:00:00-04:00"))
    r = load(d)
    assert r.ok, r.errors
    field, why = mdfeeds.prior_close(r, "JPM", now=NOW)
    assert field is None and why and "MD-19-STALE-PRIOR" in why
    fresh = load(FIX / "valid" / DATE)
    field, why = mdfeeds.prior_close(fresh, "JPM", now=NOW)
    assert why is None and field["value"] == 100.5


def test_history_reasons_are_on_the_decision_path():
    r = load(FIX / "valid" / DATE)
    from optpipe import nyse_calendar as cal
    last = cal.last_completed_session(NOW)
    ahead = last + timedelta(days=1)
    while cal.is_session(ahead) is not True:
        ahead += timedelta(days=1)
    why = mdfeeds.decision_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",), iv_history_dates=[last, ahead])
    assert any("MD-19-LOOKAHEAD" in e for e in why)
    why2 = mdfeeds.decision_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",),
                                    iv_history_dates=[last - timedelta(days=5)])
    assert any("MD-19-STALE-HISTORY" in e for e in why2)
    # an empty history is missing, not NO IDEA by itself
    bare = mdfeeds.decision_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",), iv_history_dates=[])
    assert not any("MD-19-LOOKAHEAD" in e or "MD-19-STALE-HISTORY" in e for e in bare)


def test_risk_calendar_knows_2027_holidays_the_app_calendar_knows():
    from optpipe.risk.engine import Calendar
    from optpipe.risk.ruleset import load_ruleset
    from optpipe.spec import discover
    rules, _notes = load_ruleset(discover(SPEC))
    rc = Calendar(rules.config.market_hours)
    assert rc.is_trading_day(date(2027, 1, 1)) is False
    assert rc.close_dt(date(2027, 11, 26)).hour == 13
    assert rc.is_trading_day(date(2026, 10, 20)) is True
    assert rc.is_trading_day(date(2026, 7, 3)) is False
    r = load(FIX / "valid" / DATE)
    for day in (date(2027, 1, 1), date(2027, 1, 4), date(2027, 11, 26)):
        why = mdfeeds.calendar_reasons(r, now=datetime(day.year, day.month, day.day, 12, 0, tzinfo=NY),
                                       risk_market_hours=rules.config.market_hours)
        assert not any("disagree" in e for e in why), (day, why)


# -------------------------------------------------------------------------- 5
def test_pack_discovery_selects_pack_tickers_and_names_the_pack(tmp_path):
    from optpipe.discovery.agent import DiscoveryAgent, screen_section
    rows = [ScreenRow(ticker="JPM", price=101.25), ScreenRow(ticker="WDC", price=40.0),
            ScreenRow(ticker="XMPLA", price=1.0), ScreenRow(ticker="XMPLB", price=2.0)]
    screen = ScreenResult(screen="pack", status="ok", rows=rows, source="urn:optpipe:pack:2026-10-20", fetched_at=NOW)
    sec, src = screen_section(screen, "ta filters", NOW)
    assert src["publisher"] == "Oodle pack"
    assert "Finviz" not in json.dumps(src) and "Finviz" not in json.dumps(sec)
    assert src["id"].startswith("src-pack")
    assert src["url"] == "urn:optpipe:pack:2026-10-20"
    agent = DiscoveryAgent(None, None, "")
    empty = agent.select(DiscoveryInput(as_of=NOW, run_id="run-pack", screens=["pack"], watchlist=[],
                                        max_candidates=8), {"pack": screen})
    assert {p.symbol for p in empty} == {"JPM", "WDC", "XMPLA", "XMPLB"}
    mixed = agent.select(DiscoveryInput(as_of=NOW, run_id="run-pack", screens=["pack"],
                                        watchlist=["JPM", "NOTINPACK"], max_candidates=8), {"pack": screen})
    got = {p.symbol for p in mixed}
    assert "JPM" in got and "NOTINPACK" not in got and "WDC" in got
    assert all("Finviz" not in c.text for p in mixed for c in p.catalysts)


# -------------------------------------------------------------------------- 6
def test_loaded_snapshot_is_frozen_and_quote_cache_does_not_stick(tmp_path):
    d, r = _passing_jpm(tmp_path)
    with pytest.raises(TypeError):
        r.snapshots["JPM"]["last_price"]["value"] = 1.23
    leaf = r.snapshots["JPM"]["last_price"]
    with pytest.raises(TypeError):
        dict.update(leaf, {"value": 8.5})
    assert leaf["value"] == 101.25
    m = PackMarket(r, now=NOW, allow_labels=("EXAMPLE",))
    assert m.get_quote("JPM").last == 101.25
    from optpipe.watcher.runtime import PackQuoteSource
    src = PackQuoteSource(d.parent, allow_labels=("EXAMPLE",), spec=SPEC)
    early = datetime(2026, 10, 20, 8, 0, tzinfo=NY)
    assert src._market(early) is None
    assert "2026-10-20" not in src._cache
    later = src._market(NOW)
    assert later is not None and later.res.ok
    assert later.get_quote("JPM").last == 101.25


def test_missing_macro_source_as_of_does_not_crash_research(tmp_path):
    """A macro row with events but no parseable sources[].as_of must not crash optpipe run."""
    from optpipe.cli import main
    from optpipe.providers.macro import CalendarSnapshot, visible
    d = copy_pack(tmp_path)

    def drop_as_of(doc):
        for c in doc["catalysts"]:
            if c.get("catalyst_type") == "fomc":
                for s in c.get("sources") or []:
                    s.pop("as_of", None)

    edit_json(d, "catalysts.json", drop_as_of)
    r = load(d)
    assert r.ok, r.errors
    snaps = pack_macro_from_catalysts(r, now=NOW)
    assert all(s.source.get("fetched_at") for s in snaps)
    for s in snaps:
        s.fetched_at  # a real instant, not ""
    bad = CalendarSnapshot(
        "FOMC",
        {"id": "src-pack-fomc", "url": "urn:optpipe:pack:2026-10-20/catalysts.json",
         "kind": "economic_calendar", "fetched_at": "", "publisher": "Oodle pack"},
        [], date(2026, 10, 28), "unusable read")
    kept, notes = visible([bad], NOW)
    assert kept == [] and notes
    out = tmp_path / "out"
    rc = main(["run", "--data-dir", str(d.parent), "--as-of", NOW.isoformat(),
               "--allow-example", "--out", str(out)])
    sessions = [p for p in out.glob("session-*.json") if ".trace." not in p.name]
    assert sessions, "no session file written"
    trace = json.loads(next(out.glob("*.trace.json")).read_text())
    research = [s for s in trace["runs"][-1]["steps"] if s["stage"] == "research"]
    assert research and research[0]["error"] is None
    assert rc == 0


def test_stale_iv_is_not_a_usable_realtime_number(tmp_path):
    """A STALE pack IV must not be copied as a realtime number the watcher would store as ok."""
    from optpipe.watcher.runtime import _f
    _d, r = _passing_jpm(tmp_path)
    assert r.snapshots["JPM"]["iv"]["status"] == "STALE"
    m = PackMarket(r, now=NOW, allow_labels=("EXAMPLE",), macro=pack_macro_from_catalysts(r, now=NOW))
    ch = m.get_chain("JPM")
    assert ch.quotes
    assert ch.quotes[0].iv is None
    stored = _f(ch.quotes[0].iv, ch.quotes[0].quote_time, "src-pack")
    assert stored["value"] is None and stored["status"] == "missing"
