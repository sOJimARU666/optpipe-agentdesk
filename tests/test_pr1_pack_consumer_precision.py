"""FEAT-OODLE-01 precision slice 1: pack-consumer matrix and failure twins.

Exercises discovery, research and the watcher on the pack path (and labels the fixture run and the replay
watcher as non-pack paths). Offline fixtures only. A green result here is not R7, MD-18, or R6 closure, and
it does not delete HTTP fetchers (PR C).

Ambiguous free-text macro_release notes (a sentence that merely contains CPI, PPI or NFP) stay OPEN. The
adopted rule is that an explicit note token names the kind; this file does not tighten or relax that rule.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mdpack import DATE, FIX, NOW, SPEC, copy_pack, edit_json, ids, load, rehash
from optpipe import marketdata as md
from optpipe import mdfeeds, nyse_calendar as cal
from optpipe.models.contracts import DiscoveryInput, Pick, ResearchInput
from optpipe.providers.base import DataUnavailable
from optpipe.providers.pack import PackMarket, load_pack_market, pack_macro_from_catalysts
from optpipe.research.iv import IvHistoryStore
from test_marketdata_r7 import _fresh_contract, _jpm_event_time

NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "2026-10-13"
TAPE = FIXTURES / "watch_tape.yaml"
SAMPLE = datetime(2026, 10, 20, 15, 55, tzinfo=NY)   # regular close 16:00 minus the shadow sample
FAR = datetime(2028, 1, 5, 12, 0, tzinfo=NY)
INSIDE = datetime(2027, 12, 30, 12, 0, tzinfo=NY)
ALLOW = ("MARKET", "EXAMPLE", "TEST")


def _ban_http(monkeypatch):
    from optpipe.providers.fetchers import HttpFetcher
    from optpipe.watcher.runtime import LiveNasdaqSource

    def boom(*_a, **_k):
        raise AssertionError("pack consumer issued an HTTP call")

    monkeypatch.setattr(HttpFetcher, "get", boom)
    monkeypatch.setattr(LiveNasdaqSource, "snapshot", boom)


def _ban_wall_clock(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("pack selection read the wall clock")

    monkeypatch.setattr(md, "_wall_clock", boom)


def _passing(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", _fresh_contract)
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    assert r.ok, r.errors
    return d, r


def _market(r, *, now=NOW, hold_end=None):
    return PackMarket(r, now=now, hold_end=hold_end, allow_labels=ALLOW,
                      macro=pack_macro_from_catalysts(r, now=now))


def _run_pack(root, now=NOW):
    from optpipe.config import load_settings
    from optpipe.orchestrator import Orchestrator, SessionInputs, build_pipeline
    settings = load_settings()
    pipe = build_pipeline(settings, None, SessionInputs(), now, data_dir=root, allow_labels=ALLOW)
    orch = Orchestrator(pipe)
    rec, trace, notes = orch.run(SessionInputs(), now)
    return pipe, orch, rec, trace, notes


def _research(m, store=None):
    from optpipe.research.agent import ResearchAgent
    agent = ResearchAgent(m, m, m, macro=m.macro_provider, iv_store=store, data_mode="packs")
    out = agent.run(ResearchInput(as_of=m.now, run_id="run-pack", picks=[Pick(symbol="JPM")],
                                  strike_window_pct=10))
    return out


def _discovery(m):
    from optpipe.discovery.agent import DiscoveryAgent
    from optpipe.providers.global_snapshot import UnavailableGlobalSnapshot
    agent = DiscoveryAgent(m, UnavailableGlobalSnapshot(["SPX"]), "")
    return agent.run(DiscoveryInput(as_of=m.now, run_id="run-pack", screens=["pack"], watchlist=[],
                                    max_candidates=8))


def _jpm_row_price(disc):
    raw = disc.model_dump(mode="json")
    for sec in raw["premarket_scan"]["screens"]:
        for row in sec["rows"]:
            if row["ticker"] == "JPM":
                return (row.get("last") or {}).get("value")
    return None


def _quote_status(out):
    raw = out.model_dump(mode="json")
    return raw["candidates"][0]["snapshots"][0]["quote"]["last"]


def _history_field(out):
    raw = out.model_dump(mode="json")
    return raw["candidates"][0]["snapshots"][0]["iv"]["iv30_history"]


def _macro_tokens(doc):
    """Explicit note tokens. Kind is the note itself (CPI / PPI / NFP), not a sentence around it."""
    src = {"source": "example_calendar", "url": "https://calendar.example.org/macro",
           "as_of": "2026-10-20T08:30:00-04:00", "origin": "data_vendor", "origin_entity": "Example Calendar Co"}
    for kind, ed in (("CPI", "2026-11-10"), ("PPI", "2026-11-13"), ("NFP", "2026-11-06")):
        doc["catalysts"].append({
            "ticker": None, "catalyst_type": "macro_release", "event_date": ed,
            "event_time": f"{ed}T08:30:00-05:00", "status": "UNCONFIRMED",
            "sources": [dict(src)], "note": kind,
        })


def _session_notes(rec):
    return "\n".join(rec["session"]["notes"])


def _shift_built_day(doc, day: str):
    """Move pack_built_at and same-morning as_of onto `day` so MD-01-DATE and MD-05 still hold."""
    if isinstance(doc, dict):
        for k, v in list(doc.items()):
            if k in ("as_of", "pack_built_at") and isinstance(v, str) and v.startswith("2026-10-20T"):
                doc[k] = day + "T" + v[len("2026-10-20T"):]
            else:
                _shift_built_day(v, day)
    elif isinstance(doc, list):
        for v in doc:
            _shift_built_day(v, day)


def _place(src: Path, root: Path, day: str) -> Path:
    import shutil
    dest = root / day
    shutil.copytree(src, dest)
    for p in [dest, *dest.iterdir()]:
        if not p.is_symlink():
            p.chmod(0o755 if p.is_dir() else 0o644)
    for name in dest.glob("*.json"):
        doc = json.loads(name.read_text())
        _shift_built_day(doc, day)
        if name.name in ("index.json", "catalysts.json"):
            doc["date"] = day
        name.write_text(json.dumps(doc, indent=2) + "\n")
    rehash(dest)
    return dest


def _two_dates(tmp_path):
    """2026-10-20 keeps last 101.25. 2026-10-21 is a later pack whose JPM last is 77."""
    d, _r = _passing(tmp_path)
    later = _place(d, d.parent, "2026-10-21")
    edit_json(later, "JPM.json", lambda x: x["last_price"].update(value=77.0))
    return d.parent


# -------------------------------------------------------------------------- consumer matrix
def test_matrix_run_data_dir_is_the_pack_consumer(tmp_path, monkeypatch):
    """run --data-dir: PackMarket, no HTTP, Oodle pack publisher, fixture Finviz path not used."""
    _ban_http(monkeypatch)
    _ban_wall_clock(monkeypatch)
    d, _r = _passing(tmp_path)
    from optpipe.cli import main
    out = tmp_path / "out"
    rc = main(["run", "--data-dir", str(d.parent), "--as-of", NOW.isoformat(),
               "--allow-example", "--out", str(out)])
    sessions = [p for p in out.glob("session-*.json") if ".trace." not in p.name]
    assert sessions, "no session file written"
    rec = json.loads(sessions[0].read_text())
    trace = json.loads(next(out.glob("*.trace.json")).read_text())
    assert rec["schema_version"] == "1.1.8"
    assert rec["session"]["date"] == DATE
    assert "data mode: packs" in _session_notes(rec)
    assert "fixture replay" not in _session_notes(rec)
    assert trace["runs"][-1]["data_mode"] == "packs"
    stages = {s["stage"]: s["error"] for s in trace["runs"][-1]["steps"]}
    assert stages["discovery"] is None and stages["research"] is None
    pubs = {s.get("publisher") for s in rec["sources"]}
    assert "Oodle pack" in pubs
    assert "Finviz" not in pubs
    pipe, orch, _rec2, _tr, notes = _run_pack(d.parent)
    assert pipe.data_mode == "packs"
    assert type(pipe.pack).__name__ == "PackMarket"
    assert type(pipe.fetcher).__name__ == "_NoFetch"
    assert orch.nasdaq is None
    assert any("NO IDEA" in n or "pack" in n for n in notes)
    assert rc in (0, 1)


def test_matrix_fixture_run_is_not_a_pack_consumer(pipeline_output):
    """Label, not equivalence: the fixture CLI run is the HTML/JSON provider path."""
    rec = pipeline_output["record"]
    assert "data mode: fixtures" in _session_notes(rec)
    assert "fixture replay" in _session_notes(rec)
    pubs = {s.get("publisher") for s in rec["sources"]}
    assert "Finviz" in pubs
    assert "Oodle pack" not in pubs
    trace = json.loads(next(Path(pipeline_output["out"]).glob("*.trace.json")).read_text())
    assert trace["runs"][-1]["data_mode"] == "fixtures"


def test_matrix_watch_data_dir_uses_a_fixed_clock_and_pack_files(pipeline_output, tmp_path, monkeypatch, capsys):
    """watch --data-dir --clock: simulated clock, file quotes, no live Nasdaq, EXAMPLE stays NO IDEA."""
    _ban_http(monkeypatch)
    _ban_wall_clock(monkeypatch)
    d = copy_pack(tmp_path)
    from optpipe.cli import main
    from optpipe.watcher.runtime import PackQuoteSource
    sess = pipeline_output["out"] / "session-2026-10-13.json"
    out = tmp_path / "watch"
    rc = main(["watch", "--session", str(sess), "--data-dir", str(d.parent),
               "--clock", SAMPLE.isoformat(), "--once", "--out", str(out)])
    captured = capsys.readouterr()
    assert rc == 0, captured.out + captured.err
    doc = json.loads((out / "session-2026-10-13.watch.json").read_text())
    assert doc["mode"] == {"clock": "simulated", "quotes": "pack", "quote_method": "file",
                           "tape": None, "tape_label": None}
    assert doc["window"]["start"]["ny"].startswith("2026-10-20T15:55")
    assert doc["wakes"][0]["market"] == "open"
    assert doc["summary"]["quote_calls"] == 1
    assert doc["wakes"][0]["quote_calls"] == 1
    from optpipe.watcher.runtime import validate_watch
    assert validate_watch(doc) == []
    rejected = json.loads(json.dumps(doc))
    rejected["mode"]["quote_method"] = "POST"
    assert any("quote_method" in e for e in validate_watch(rejected))
    src = PackQuoteSource(d.parent, spec=SPEC)          # CLI default: MARKET only, so EXAMPLE is NO IDEA
    snap = src.snapshot(SAMPLE, ["JPM"], [])
    assert src.calls[0]["method"] == "file"
    assert snap["underlyings"]["JPM"]["last"]["status"] == "missing"
    assert snap["underlyings"]["JPM"]["last"]["value"] is None
    naive = main(["watch", "--session", str(sess), "--data-dir", str(d.parent),
                  "--clock", "2026-10-20T15:55:00", "--once", "--out", str(tmp_path / "naive")])
    assert naive == 2
    assert "UTC offset" in capsys.readouterr().err


def test_matrix_replay_watch_is_not_a_pack_consumer(pipeline_output, tmp_path, monkeypatch, capsys):
    """Replay is the hand-made tape. It is not a validated pack and it does not open LiveNasdaqSource."""
    _ban_http(monkeypatch)
    from optpipe.cli import main
    sess = pipeline_output["out"] / "session-2026-10-13.json"
    rc = main(["watch", "--session", str(sess), "--replay", str(TAPE),
               "--clock", "2026-10-13T15:55:00-04:00", "--once", "--out", str(tmp_path)])
    assert rc == 0, capsys.readouterr().out
    doc = json.loads((tmp_path / "session-2026-10-13.watch.json").read_text())
    assert doc["mode"]["quotes"] == "replay"
    assert doc["mode"]["quote_method"] == "GET"
    assert doc["mode"]["clock"] == "simulated"
    assert doc["mode"]["quotes"] != "pack"


# -------------------------------------------------------------------------- missing / invalid pack
def test_missing_pack_fails_closed_for_discovery_research_and_watcher(tmp_path, monkeypatch):
    _ban_http(monkeypatch)
    _ban_wall_clock(monkeypatch)
    root = tmp_path / "empty"
    root.mkdir()
    from optpipe.cli import main
    from optpipe.watcher.runtime import PackQuoteSource
    out = tmp_path / "out"
    rc = main(["run", "--data-dir", str(root), "--as-of", NOW.isoformat(),
               "--allow-example", "--out", str(out)])
    rec = json.loads(next(p for p in out.glob("session-*.json") if ".trace." not in p.name).read_text())
    blob = json.dumps(rec)
    assert "data mode: packs" in _session_notes(rec)
    assert "FAIL CLOSED" in blob
    assert rec["candidates"] == []
    assert "Finviz" not in blob
    assert rc in (0, 1)
    pipe, _orch, rec2, _tr, notes = _run_pack(root)
    assert not pipe.pack.res.ok
    assert any("FAIL CLOSED" in n for n in notes)
    assert rec2["candidates"] == []
    disc = _discovery(pipe.pack)
    assert disc.picks == []
    assert any("pack" in n for n in disc.notes)
    with pytest.raises(DataUnavailable, match="NO IDEA|not ok"):
        pipe.pack.get_quote("JPM")
    with pytest.raises(DataUnavailable):
        pipe.pack.get_chain("JPM")
    src = PackQuoteSource(root, allow_labels=ALLOW, spec=SPEC)
    snap = src.snapshot(NOW, ["JPM"], ["JPM"])
    assert snap["underlyings"]["JPM"]["last"]["status"] == "missing"
    assert src.calls[0]["method"] == "file"


def test_invalid_pack_fails_closed_for_discovery_research_and_watcher(tmp_path, monkeypatch):
    _ban_http(monkeypatch)
    _ban_wall_clock(monkeypatch)
    d = copy_pack(tmp_path)
    (d / "JPM.json").write_text("{")
    rehash(d)
    r = load(d)
    assert not r.ok and "MD-03-JSON" in ids(r.errors)
    pipe, _orch, rec, _tr, notes = _run_pack(d.parent)
    assert not pipe.pack.res.ok
    assert any("FAIL CLOSED" in n for n in notes)
    assert rec["candidates"] == []
    screen = pipe.pack.get_screen("pack")
    assert screen.status == "error" and screen.rows == []
    disc = _discovery(pipe.pack)
    assert disc.picks == []
    with pytest.raises(DataUnavailable):
        pipe.pack.get_quote("JPM")
    from optpipe.watcher.runtime import PackQuoteSource
    src = PackQuoteSource(d.parent, allow_labels=ALLOW, spec=SPEC)
    snap = src.snapshot(NOW, ["JPM", "WDC"], [])
    assert snap["underlyings"]["JPM"]["last"]["value"] is None
    assert snap["underlyings"]["WDC"]["last"]["value"] is None
    assert "Finviz" not in json.dumps(rec)


# -------------------------------------------------------------------------- staleness / freshness
def test_stale_quote_is_no_idea_on_research_and_watcher(tmp_path):
    """08:55 is fresh at 09:05 and stale at 09:40. The pack still loads: MD-05 is versus pack_built_at."""
    d, fresh = _passing(tmp_path)
    assert not any("MD-19-STALE-quote" in e for e in _market(fresh).reasons("JPM", hold_end=None))
    later = datetime(2026, 10, 20, 9, 40, tzinfo=NY)
    r = load(d, now=later)
    assert r.ok, r.errors
    m = _market(r, now=later)
    why = m.reasons("JPM", hold_end=None)
    assert any("MD-19-STALE-quote" in e for e in why)
    with pytest.raises(DataUnavailable, match="NO IDEA"):
        m.get_quote("JPM")
    out = _research(m)
    assert any("NO IDEA" in n and "JPM" in n for n in out.notes)
    assert _quote_status(out)["status"] == "missing"
    _pipe, orch, rec, _tr, notes = _run_pack(d.parent, later)
    assert any("MD-19-STALE-quote" in e for e in orch.pack.reasons("JPM"))
    assert any("NO IDEA JPM" in n for n in notes)
    assert all(c.get("underlying") != "JPM" for c in rec["candidates"])
    from optpipe.records.fields import occ_symbol
    from optpipe.watcher.runtime import PackQuoteSource
    src = PackQuoteSource(d.parent, allow_labels=ALLOW, spec=SPEC)
    cid = occ_symbol("JPM", date(2026, 10, 23), "C", 102.0)
    snap = src.snapshot(later, ["JPM"], [cid])
    assert snap["underlyings"]["JPM"]["last"]["status"] == "missing"
    assert snap["options"][cid]["bid"]["value"] is None


def test_fresh_quote_is_not_marked_stale_on_the_pack_consumer(tmp_path):
    """Pass twin: a quote inside the 20-minute pack limit is not MD-19-STALE-quote."""
    d, r = _passing(tmp_path)
    m = _market(r)
    assert not any("MD-19-STALE-quote" in e for e in m.reasons("JPM", hold_end=None))
    q = m.get_quote("JPM")
    assert q.last == 101.25 and q.is_realtime is True
    out = _research(m)
    last = _quote_status(out)
    assert last["status"] == "ok" and last["value"] == 101.25
    _pipe, orch, _rec, _tr, notes = _run_pack(d.parent)
    assert not any("MD-19-STALE-quote" in e for e in orch.pack.reasons("JPM"))
    assert not any("MD-19-STALE-quote" in n for n in notes)


def test_stale_macro_and_prior_close_do_not_become_fresh_inputs(tmp_path):
    d, _r = _passing(tmp_path)

    def old_fomc(doc):
        for c in doc["catalysts"]:
            if c.get("catalyst_type") == "fomc":
                for s in c["sources"]:
                    s["as_of"] = "2026-01-02T09:00:00-05:00"

    edit_json(d, "catalysts.json", old_fomc)
    edit_json(d, "catalysts.json",
              lambda doc: doc["ticker_context"][0]["prior_close"].update(as_of="2026-10-19T10:00:00-04:00"))
    r = load(d)
    assert r.ok, r.errors
    m = _market(r)
    field, why = mdfeeds.prior_close(r, "JPM", now=NOW)
    assert field is None and why and "MD-19-STALE-PRIOR" in why
    q = m.get_quote("JPM")
    assert q.last == 101.25 and q.prev_close is None
    out = _research(m)
    raw = out.model_dump(mode="json")
    prev = raw["candidates"][0]["snapshots"][0]["quote"]["prev_close"]
    assert prev["status"] == "missing" and prev["value"] is None
    hold = cal.close_at(date(2026, 10, 20))
    gated = m.reasons("JPM", hold_end=hold)
    assert any("MD-19-STALE-MACRO" in e and "FOMC" in e for e in gated)
    _pipe, orch, rec, _tr, notes = _run_pack(d.parent)
    assert any("MD-19-STALE-MACRO" in e for e in orch.pack.reasons("JPM"))
    assert any("NO IDEA JPM" in n for n in notes)
    assert all(c.get("underlying") != "JPM" for c in rec["candidates"])


def test_explicit_macro_note_tokens_still_name_cpi_ppi_nfp(tmp_path):
    """Adopted pass twin: note is exactly CPI, PPI or NFP. Ambiguous sentences stay OPEN and are not asserted."""
    d, _r = _passing(tmp_path)
    edit_json(d, "catalysts.json", _macro_tokens)
    r = load(d)
    assert r.ok, r.errors
    m = _market(r)
    kinds = {s.kind for s in m.macro}
    assert kinds == {"FOMC", "CPI", "PPI", "NFP"}
    out = _research(m)
    raw = out.model_dump(mode="json")
    covered = {c["kind"]: c["checked_through"]["status"]
               for c in raw["event_calendar"]["coverage"] if c["underlying"] is None}
    for kind in ("FOMC", "CPI", "PPI", "NFP"):
        assert covered[kind] == "ok"
    hold = cal.close_at(date(2026, 10, 20))
    why = m.reasons("JPM", hold_end=hold)
    assert not any("no macro calendar" in e for e in why)
    assert not any("R08" in e for e in why)


# -------------------------------------------------------------------------- fixed clock / no lookahead / calendar boundary
def test_fixed_clock_selects_the_pack_without_the_wall_clock(tmp_path, monkeypatch):
    _ban_wall_clock(monkeypatch)
    d = copy_pack(tmp_path)
    root = FIX / "valid"
    early = datetime(2026, 10, 20, 8, 0, tzinfo=NY)
    assert not mdfeeds.select_pack(root, early, spec=SPEC).ok
    assert "MD-19-FUTURE-PACK" in ids(mdfeeds.select_pack(root, early, spec=SPEC).errors)
    assert mdfeeds.select_pack(root, NOW, spec=SPEC).ok
    from optpipe.watcher.runtime import PackQuoteSource
    src = PackQuoteSource(d.parent, allow_labels=ALLOW, spec=SPEC)
    snap = src.snapshot(early, ["JPM"], [])
    assert snap["underlyings"]["JPM"]["last"]["status"] == "missing"
    assert src._market(early) is None
    from optpipe.cli import main
    out = tmp_path / "out"
    rc = main(["run", "--data-dir", str(d.parent), "--as-of", NOW.isoformat(),
               "--allow-example", "--out", str(out)])
    rec = json.loads(next(p for p in out.glob("session-*.json") if ".trace." not in p.name).read_text())
    assert rec["session"]["date"] == DATE
    assert rc in (0, 1)
    naive = mdfeeds.select_pack(root, datetime(2026, 10, 20, 9, 5), spec=SPEC)
    assert not naive.ok and "MD-19-TZ" in ids(naive.errors)


def test_iv_history_lookahead_is_no_idea_on_the_pack_run(tmp_path, monkeypatch):
    d, r = _passing(tmp_path)
    last = cal.last_completed_session(NOW)
    assert last is not None
    ahead = last + timedelta(days=1)
    while cal.is_session(ahead) is not True:
        ahead += timedelta(days=1)
        if ahead.year > 2027:
            pytest.fail("no later session in coverage")
    store = IvHistoryStore(tmp_path / "iv")
    store.record("JPM", ahead, 0.31, NOW, "hostile", "packs")
    m = _market(r)
    m.iv_store = store
    assert any("MD-19-LOOKAHEAD" in e for e in m.reasons("JPM", hold_end=None))
    with pytest.raises(DataUnavailable, match="NO IDEA"):
        m.get_quote("JPM")
    # research, if handed the pick anyway, must not present the future series as usable history
    # Pack research has no Treasury rate, so the displayed IV history stays "no current IV".
    # The adopted lookahead gate is decision_reasons, which the run below applies before a pick is kept.
    shown = _research(_market(r), store)
    assert _history_field(shown)["status"] == "missing"
    monkeypatch.setenv("OPTPIPE_STATE_DIR", str(tmp_path / "state"))
    hist_dir = Path(os.environ["OPTPIPE_STATE_DIR"]) / "packs" / "iv_history"
    hist_dir.mkdir(parents=True)
    (hist_dir / "JPM.jsonl").write_text(json.dumps({
        "date": ahead.isoformat(), "iv30": 0.31, "recorded_at": NOW.isoformat(),
        "run_id": "hostile", "data_mode": "packs"}) + "\n")
    _pipe, orch, rec, _tr, notes = _run_pack(d.parent)
    assert any("MD-19-LOOKAHEAD" in e for e in orch.pack.reasons("JPM"))
    assert any("NO IDEA JPM" in n for n in notes)
    assert all(c.get("underlying") != "JPM" for c in rec["candidates"])


def test_later_pack_is_not_visible_at_an_earlier_clock(tmp_path, monkeypatch):
    """No lookahead across pack dates: discovery, research and the watcher at T do not read T+1."""
    _ban_wall_clock(monkeypatch)
    root = _two_dates(tmp_path)
    early = load_pack_market(root, DATE, now=NOW, allow_labels=ALLOW, spec=SPEC)
    later_now = datetime(2026, 10, 21, 9, 5, tzinfo=NY)
    late = load_pack_market(root, "2026-10-21", now=later_now, allow_labels=ALLOW, spec=SPEC)
    assert early.res.snapshots["JPM"]["last_price"]["value"] == 101.25
    assert late.res.snapshots["JPM"]["last_price"]["value"] == 77.0
    assert _jpm_row_price(_discovery(early)) == 101.25
    assert _jpm_row_price(_discovery(late)) == 77.0
    q = early.get_quote("JPM")
    assert q.last == 101.25
    assert late.get_quote("JPM").last == 77.0
    from optpipe.watcher.runtime import PackQuoteSource
    src = PackQuoteSource(root, allow_labels=ALLOW, spec=SPEC)
    now_m = src._market(NOW)
    next_m = src._market(later_now)
    assert now_m.res.date == DATE and now_m.res.snapshots["JPM"]["last_price"]["value"] == 101.25
    assert next_m.res.date == "2026-10-21" and next_m.res.snapshots["JPM"]["last_price"]["value"] == 77.0
    snap = src.snapshot(NOW, ["JPM"], [])
    assert snap["underlyings"]["JPM"]["last"]["value"] != 77.0


def test_2028_is_outside_calendar_and_2027_is_not_that_failure(tmp_path):
    """App-calendar UNKNOWN at 2028 is NO IDEA on the pack consumers. 2027 is still inside coverage."""
    assert cal.covers(INSIDE.date()) is True
    assert cal.covers(FAR.date()) is False
    d = copy_pack(tmp_path)
    dated = tmp_path / "years"
    dated.mkdir()
    _place(d, dated, "2027-12-30")
    _place(d, dated, "2028-01-05")
    inside = load_pack_market(dated, "2027-12-30", now=INSIDE, allow_labels=ALLOW, spec=SPEC)
    outside = load_pack_market(dated, "2028-01-05", now=FAR, allow_labels=ALLOW, spec=SPEC)
    assert inside.res.ok and outside.res.ok
    assert not any("outside the app calendar" in e for e in inside.reasons("JPM"))
    assert any("MD-19-CALENDAR" in e and "outside the app calendar" in e for e in outside.reasons("JPM"))
    disc = _discovery(outside)
    assert any(p.symbol == "JPM" for p in disc.picks)          # the screen lists the index
    assert not outside.usable("JPM")
    with pytest.raises(DataUnavailable, match="NO IDEA"):
        outside.get_quote("JPM")
    out = _research(outside)
    assert _quote_status(out)["status"] == "missing"
    assert any("NO IDEA" in n for n in out.notes)
    from optpipe.watcher.runtime import PackQuoteSource
    src = PackQuoteSource(dated, allow_labels=ALLOW, spec=SPEC)
    snap = src.snapshot(FAR, ["JPM"], [])
    assert snap["underlyings"]["JPM"]["last"]["status"] == "missing"
    assert any("outside the app calendar" in e for e in src._market(FAR).reasons("JPM"))
    assert not any("outside the app calendar" in e for e in src._market(INSIDE).reasons("JPM"))
