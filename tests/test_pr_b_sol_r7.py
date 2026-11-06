"""Sol R7 evidence for FEAT-OODLE-01 PR B (SOL-REVIEW-2026-11-06-0035).

Maps to Sol's seven required demonstrations. Offline fixtures only; OPTIONS_SPEC_DIR=spec.
Does NOT claim R6 closure, MD-18 closure, or network isolation. PR C (fetcher deletion) is not in scope.
A green result here is evidence for the properties the tests actually execute. It does not close Sol R7.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mdpack import DATE, FIX, NOW, ROOT, SPEC, copy_pack, edit_json, edit_text, ids, load, rehash
from optpipe import mdfeeds, marketdata as md
from optpipe.providers.pack import PackMarket
from test_marketdata_r7 import _fresh_contract, _jpm_event_time

NY = ZoneInfo("America/New_York")
SRC = ROOT / "src" / "optpipe"


# 1. Discovery / research / watcher on the pack path use the loaded pack, not the live fetchers.
def test_r7_1_pack_run_uses_pack_market_not_live_fetchers(tmp_path):
    from optpipe.orchestrator import Orchestrator, SessionInputs, build_pipeline
    from optpipe.providers.base import DataUnavailable
    from optpipe.providers.fetchers import HttpFetcher
    from optpipe.config import load_settings
    d = copy_pack(tmp_path)
    settings = load_settings()
    inputs = SessionInputs()
    pipe = build_pipeline(settings, None, inputs, NOW, data_dir=d.parent, allow_labels=("EXAMPLE",))
    assert pipe.data_mode == "packs"
    assert type(pipe.pack).__name__ == "PackMarket"
    assert type(pipe.fetcher).__name__ == "_NoFetch"
    assert not isinstance(pipe.fetcher, HttpFetcher)
    assert hasattr(pipe.pack.macro_provider, "get_calendars")
    orch = Orchestrator(pipe)
    assert orch.nasdaq is None
    rec, trace, _notes = orch.run(inputs, NOW)
    stages = [(s.stage, s.error) for s in trace.steps]
    assert ("research", None) in stages
    assert ("discovery", None) in stages
    assert rec["session"]["date"] == DATE
    pubs = {s.get("publisher") for s in rec["sources"]}
    assert "Oodle pack" in pubs
    assert "Finviz" not in pubs
    with pytest.raises(DataUnavailable):
        pipe.fetcher.get("macro", "fomc", "https://example.invalid/nope")


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

    def stale_bmo(x):
        _fresh_contract(x)
        x["earnings_time"].update(status="STALE", value="BMO")

    edit_json(d, "JPM.json", stale_bmo)
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    assert r.ok, r.errors
    why = md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",))
    dec = mdfeeds.decision_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",))
    assert ids(why) == {"MD-20-CRITICAL"}
    assert any("earnings_time" in e and "STALE" in e for e in why)
    assert any("earnings_time" in e and "STALE" in e for e in dec)
    assert not any("MD-20-UNVERIFIED" in e for e in why)
    d2 = copy_pack(tmp_path / "x")
    edit_json(d2, "JPM.json", lambda x: x["earnings_date"].update(value="2026-10-22"))
    r2 = load(d2)
    assert not r2.ok and "MD-20-CONFLICT" in ids(r2.errors)


# 4. Source-specific freshness + calendar / skew / no-lookahead
def test_r7_4_freshness_calendar_skew_lookahead_on_the_decision_path(tmp_path):
    assert md.DEFAULT_SKEW_SECONDS == 0
    r = load(FIX / "valid" / DATE)
    far = datetime(2028, 1, 5, 12, 0, tzinfo=NY)
    assert any("MD-19-CALENDAR" in e for e in mdfeeds.decision_reasons(
        r, "JPM", now=far, allow_labels=("EXAMPLE",)))
    from optpipe import nyse_calendar as cal
    last = cal.last_completed_session(NOW)
    assert last is not None
    ahead = last + timedelta(days=1)
    while cal.is_session(ahead) is not True:
        ahead += timedelta(days=1)
        if ahead.year > 2027:
            pytest.fail("no later session in coverage")
    why = mdfeeds.decision_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",), iv_history_dates=[last, ahead])
    assert any("MD-19-LOOKAHEAD" in e for e in why)
    why2 = mdfeeds.decision_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",),
                                    iv_history_dates=[last - timedelta(days=5)])
    assert any("MD-19-STALE-HISTORY" in e for e in why2)
    d = copy_pack(tmp_path)
    edit_json(d, "catalysts.json",
              lambda doc: doc["ticker_context"][0]["prior_close"].update(as_of="2026-10-19T10:00:00-04:00"))
    early = load(d)
    assert early.ok
    field, reason = mdfeeds.prior_close(early, "JPM", now=NOW)
    assert field is None and reason and "MD-19-STALE-PRIOR" in reason


# 5. Traceable provenance (MD-18 residual: integrity only; documented OPEN)
def test_r7_5_provenance_is_integrity_only_and_md18_stays_open(tmp_path):
    """MD-18 is OPEN. A recomputed pack.sha256 makes hostile bytes load: that is integrity, not provenance."""
    assert md.MD18_PROVENANCE == "OPEN"
    assert "pack.sha256" not in (SRC / "orchestrator.py").read_text()
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"value": 101.25', '"value": 999.5')
    r = load(d)
    assert r.ok, r.errors
    assert r.snapshots["JPM"]["last_price"]["value"] == 999.5


# 6. Filesystem / race protections (carried from PR A; still enforced)
def test_r7_6_filesystem_and_race_protections_still_enforced(tmp_path):
    d = copy_pack(tmp_path)
    target = d / "JPM.json"
    link = d / "dup.json"
    try:
        link.hardlink_to(target)
    except OSError as ex:
        pytest.fail(f"hard links are required to demonstrate MD-17-HARDLINK ({ex})")
    digest = hashlib.sha256(link.read_bytes()).hexdigest()
    man = d / "pack.sha256"
    man.write_text(man.read_text().rstrip("\n") + f"\n{digest}  dup.json\n")
    assert target.stat().st_nlink >= 2 and link.stat().st_nlink >= 2
    r = md.load_pack(d, root=d.parent, now=NOW, spec=SPEC)
    assert not r.ok and r.exit_code() == 1
    assert "MD-17-HARDLINK" in ids(r.errors)
    # a symlink and a group/world-writable file are the same filesystem class of refusal
    d2 = copy_pack(tmp_path / "sym")
    (d2 / "JPM.json").unlink()
    (d2 / "JPM.json").symlink_to(d / "JPM.json")
    r2 = md.load_pack(d2, root=d2.parent, now=NOW, spec=SPEC)
    assert not r2.ok and "MD-01-SYMLINK" in ids(r2.errors)
    d3 = copy_pack(tmp_path / "mode")
    (d3 / "JPM.json").chmod(0o666)
    r3 = md.load_pack(d3, root=d3.parent, now=NOW, spec=SPEC)
    assert not r3.ok and "MD-17-WRITABLE" in ids(r3.errors)


# 7. Deterministic failure-case replay tests
def test_r7_7_select_pack_replay_is_deterministic_and_fail_closed(tmp_path, monkeypatch):
    root = FIX / "valid"
    clocks = [datetime(2026, 10, 20, 8, 0, tzinfo=NY), datetime(2026, 10, 20, 9, 5), NOW]
    for c in clocks:
        a = mdfeeds.select_pack(root, c, spec=SPEC)
        b = mdfeeds.select_pack(root, c, spec=SPEC)
        c3 = mdfeeds.select_pack(root, c, spec=SPEC)
        assert a.errors == b.errors == c3.errors
    assert "MD-19-FUTURE-PACK" in ids(mdfeeds.select_pack(root, clocks[0], spec=SPEC).errors)
    assert ids(mdfeeds.select_pack(root, clocks[1], spec=SPEC).errors) == {"MD-19-TZ"}
    assert mdfeeds.select_pack(root, NOW, spec=SPEC).ok

    def _boom(*_a, **_k):
        raise AssertionError("select_pack read the wall clock")

    monkeypatch.setattr(md, "_wall_clock", _boom)
    assert mdfeeds.select_pack(root, NOW, spec=SPEC).ok
    monkeypatch.undo()

    bad = copy_pack(tmp_path / "bad")
    (bad / "JPM.json").write_text("{")
    rehash(bad)
    tampered = copy_pack(tmp_path / "tamper")
    edit_text(tampered, "JPM.json", '"value": 101.25', '"value": 1.0', hash_it=False)
    oversized = copy_pack(tmp_path / "big")
    monkeypatch.setattr(md, "MAX_PACK_BYTES", 4096)
    assert "MD-16-TOTAL" in ids(load(oversized).errors)
    monkeypatch.setattr(md, "MAX_PACK_BYTES", md.MAX_PACK_BYTES if False else 64 * 1024 * 1024)

    script = r"""
import json, os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from optpipe import mdfeeds, marketdata as md
NY = ZoneInfo("America/New_York")
root = Path(os.environ["PACK_ROOT"])
spec = os.environ["SPEC"]
now = datetime(2026, 10, 20, 9, 5, tzinfo=NY)

def pack(r):
    return {"ok": bool(r.ok), "errors": list(r.errors)}

out = {
    "early": pack(mdfeeds.select_pack(root, datetime(2026, 10, 20, 8, 0, tzinfo=NY), spec=spec)),
    "naive": pack(mdfeeds.select_pack(root, datetime(2026, 10, 20, 9, 5), spec=spec)),
    "now": pack(mdfeeds.select_pack(root, now, spec=spec)),
    "trunc": pack(md.load_pack(Path(os.environ["BAD_PACK"]), root=Path(os.environ["BAD_PACK"]).parent, now=now, spec=spec)),
    "tamper": pack(md.load_pack(Path(os.environ["TAMPER_PACK"]), root=Path(os.environ["TAMPER_PACK"]).parent, now=now, spec=spec)),
}
print(json.dumps(out, sort_keys=True))
"""
    blobs = []
    for seed in ("0", "1"):
        for tz in ("UTC", "Europe/Berlin", "America/New_York"):
            env = os.environ.copy()
            env.update(PYTHONHASHSEED=seed, TZ=tz, PACK_ROOT=str(root), SPEC=str(SPEC),
                       BAD_PACK=str(bad), TAMPER_PACK=str(tampered))
            blobs.append(subprocess.check_output([sys.executable, "-c", script], env=env, cwd=str(ROOT)))
    assert len(blobs) == 6 and len(set(blobs)) == 1
    got = json.loads(blobs[0])
    assert "MD-19-FUTURE-PACK" in ids(got["early"]["errors"])
    assert ids(got["naive"]["errors"]) == {"MD-19-TZ"}
    assert got["now"]["ok"] is True
    assert "MD-03-JSON" in ids(got["trunc"]["errors"])
    assert "MD-02-HASH" in ids(got["tamper"]["errors"])
