"""BLS release calendar (steering 20:34): contact-email User-Agent from config (empty by default -> BLS skipped),
bls.ics SUMMARY parsing, FRED fallback (rid 10/46/50, US Central Time), GET only, all offline.

Doodle's dates (spec/bls_release_sources.md), all 08:30 America/New_York = 14:30 Europe/Berlin (CET):
NFP 2026-11-06, CPI 2026-11-10, PPI 2026-11-13, NFP 2026-12-04, CPI 2026-12-10, PPI 2026-12-15."""
from __future__ import annotations

import re
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from optpipe.config import SourcesCfg
from optpipe.config import load_settings
from optpipe.orchestrator import build_pipeline
from optpipe.providers.base import DataUnavailable, FetchResult
from optpipe.providers.macro import (BLS_UA_TEMPLATE, FRED_RELEASE_IDS, LiveMacroCalendars, bls_ics_snapshots,
                                     bls_user_agent, fred_snapshot, parse_bls_ics, parse_fred_calendar)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tests" / "fixtures" / "sources"
NY, BERLIN = ZoneInfo("America/New_York"), ZoneInfo("Europe/Berlin")
ICS = (SRC / "bls_schedule_handmade.ics").read_text()
FRED = {k: (SRC / f"fred_calendar_rid{rid}_2026.html").read_text() for k, rid in FRED_RELEASE_IDS.items()}
FETCHED = datetime(2026, 10, 29, 15, 50, tzinfo=NY)          # 20:50 CET, the FRED capture time
EXPECTED = {"NFP": [date(2026, 11, 6), date(2026, 12, 4)],
            "CPI": [date(2026, 11, 10), date(2026, 12, 10)],
            "PPI": [date(2026, 11, 13), date(2026, 12, 15)]}
TEST_EMAIL = "someone@example.com"                            # reserved example domain (RFC 2606), not a real inbox


def _at(d: date) -> datetime:
    return datetime.combine(d, time(8, 30), NY)


def _remaining_2026(snap):
    return [e.at for e in snap.events if e.at.year == 2026]


# ---------------------------------------------------------------- User-Agent / config
def test_user_agent_needs_a_contact_email():
    assert BLS_UA_TEMPLATE == "optpipe/1.1 (calendar fetch; contact: {email})"
    for bad in (None, "", "   ", "not-an-email", "a@b", "x y@example.com", "a@example.com)"):
        assert bls_user_agent(bad) is None, bad
    assert bls_user_agent(f"  {TEST_EMAIL} ") == f"optpipe/1.1 (calendar fetch; contact: {TEST_EMAIL})"


def test_contact_email_is_empty_by_default_in_model_and_settings(monkeypatch):
    monkeypatch.delenv("OPTPIPE_BLS_CONTACT_EMAIL", raising=False)
    assert SourcesCfg().bls_contact_email == ""
    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())
    assert raw["sources"]["bls_contact_email"] == ""
    assert load_settings().sources.bls_contact_email == ""


def test_env_override_for_contact_email(monkeypatch):
    monkeypatch.setenv("OPTPIPE_BLS_CONTACT_EMAIL", f" {TEST_EMAIL} ")
    assert load_settings().sources.bls_contact_email == TEST_EMAIL
    monkeypatch.setenv("OPTPIPE_BLS_CONTACT_EMAIL", "")
    assert load_settings().sources.bls_contact_email == ""


def test_no_real_email_address_is_hardcoded():
    pat = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    allowed = re.compile(r"@(example\.(com|org|net)|[a-z0-9.-]+\.invalid)$", re.I)
    hits = []
    for base in ("src", "config", "scripts", ".github"):
        for f in (ROOT / base).rglob("*"):
            if f.is_file() and f.suffix in {".py", ".yaml", ".yml", ".toml", ".html", ".md", ".txt"}:
                hits += [(f.relative_to(ROOT), m) for m in pat.findall(f.read_text(errors="ignore"))
                         if not allowed.search(m)]
    assert hits == []


def test_build_pipeline_no_longer_has_a_live_path(monkeypatch):
    """FEAT-OODLE-01 PR B: --live is removed; build_pipeline takes fixtures XOR data_dir. The BLS UA rule is
    still enforced by LiveMacroCalendars itself (kept for PR C); see test_live_calendars_* below."""
    monkeypatch.delenv("OPTPIPE_BLS_CONTACT_EMAIL", raising=False)
    s = load_settings()
    inputs = type("I", (), {"global_snapshot_record": None})()
    with pytest.raises(ValueError, match="exactly one"):
        build_pipeline(s, None, inputs, FETCHED)
    assert bls_user_agent(None) is None
    assert bls_user_agent(TEST_EMAIL) == f"optpipe/1.1 (calendar fetch; contact: {TEST_EMAIL})"


# ---------------------------------------------------------------- bls.ics
def test_parse_bls_ics_summaries_and_times():
    rows = parse_bls_ics(ICS)
    kinds = {(k, at.date()) for k, at, _ in rows}
    for kind, days in EXPECTED.items():
        for d in days:
            assert (kind, d) in kinds
    assert {s for _, _, s in rows} == {"Consumer Price Index", "Producer Price Index", "Employment Situation"}
    assert all(at.tzinfo is not None and at.astimezone(NY).time() == time(8, 30) for _, at, _ in rows)
    # Real Earnings / JOLTS / State Employment are not R08 events
    assert len(rows) == 10          # CPI 10-14 + six target dates + three Jan 2027


def test_ics_folded_summary_and_utc_dtstart():
    rows = {(k, at) for k, at, _ in parse_bls_ics(ICS)}
    assert ("NFP", _at(date(2026, 11, 6))) in rows                # SUMMARY folded across two lines
    assert ("NFP", _at(date(2026, 12, 4))) in rows                # DTSTART 20261204T133000Z = 08:30 EST


def test_bls_ics_snapshots_from_fetch_date():
    snaps = {s.kind: s for s in bls_ics_snapshots(ICS, "https://www.bls.gov/schedule/news_release/bls.ics", FETCHED)}
    assert set(snaps) == {"CPI", "PPI", "NFP"}
    for kind, days in EXPECTED.items():
        assert _remaining_2026(snaps[kind]) == [_at(d) for d in days]        # 10-14 CPI is before the fetch date
        assert snaps[kind].source["id"] == "src-bls-ics"
    assert snaps["CPI"].checked_through == date(2027, 1, 13)
    assert snaps["PPI"].checked_through == date(2027, 1, 14)
    assert snaps["NFP"].checked_through == date(2027, 1, 8)


def test_berlin_times_are_14_30():
    for days in EXPECTED.values():
        for d in days:
            b = _at(d).astimezone(BERLIN)
            assert (b.hour, b.minute) == (14, 30) and b.tzname() == "CET"


# ---------------------------------------------------------------- FRED fallback (real captures)
@pytest.mark.parametrize("kind,count", [("CPI", 12), ("PPI", 13), ("NFP", 12)])
def test_fred_real_capture_central_time_to_8_30_et(kind, count):
    ats = parse_fred_calendar(FRED[kind], {"CPI": "Consumer Price Index", "PPI": "Producer Price Index",
                                           "NFP": "Employment Situation"}[kind])
    assert len(ats) == count
    assert all(a.tzinfo and a.astimezone(NY).time() == time(8, 30) for a in ats)
    assert [a for a in ats if a >= FETCHED] == [_at(d) for d in EXPECTED[kind]]


def test_fred_snapshot_and_ics_agree():
    ics = {s.kind: s for s in bls_ics_snapshots(ICS, "u", FETCHED)}
    for kind in EXPECTED:
        f = fred_snapshot(kind, [(f"https://fred.stlouisfed.org/releases/calendar?rid={FRED_RELEASE_IDS[kind]}&y=2026",
                                  FRED[kind])], FETCHED)
        assert [e.at for e in f.events] == _remaining_2026(ics[kind])
        assert f.checked_through == EXPECTED[kind][-1]
        assert f.source["id"] == f"src-fred-rid{FRED_RELEASE_IDS[kind]}"


def test_fred_refuses_to_guess_the_zone():
    with pytest.raises(DataUnavailable, match="Central Time"):
        parse_fred_calendar(FRED["CPI"].replace("Central Time", "Some Time"), "Consumer Price Index")


# ---------------------------------------------------------------- live provider (fake fetchers, GET only)
class Recorder:
    """Records every (resource, key, url); serves fixtures; can block keys. Only `get` exists."""

    def __init__(self, blocked=(), ics=ICS):
        self.calls, self.blocked, self.ics = [], blocked, ics

    def get(self, resource, key, url):
        self.calls.append((resource, key, url))
        if any(b in key for b in self.blocked):
            raise DataUnavailable(f"HTTP 403 for {url}")
        if key == "fomc":
            return FetchResult((SRC / "fed_fomccalendars.htm").read_text(), url, FETCHED, "live")
        if key == "bls-ics":
            return FetchResult(self.ics, url, FETCHED, "live")
        if key.startswith("fred-"):
            _, kind, year = key.split("-")
            if year != "2026":
                raise DataUnavailable(f"no {year} capture")
            return FetchResult(FRED[kind.upper()], url, FETCHED, "live")
        raise AssertionError(f"unexpected fetch {key} {url}")


def _kinds(snaps):
    return {s.kind: s for s in snaps}


def test_no_email_never_contacts_bls_and_uses_fred():
    main = Recorder()
    snaps, notes = LiveMacroCalendars(main, "https://fed/fomc", bls_fetcher=None).get_calendars(FETCHED)
    assert not any("bls.gov" in u for _, _, u in main.calls)
    assert any("BLS live fetch skipped" in n and "bls_contact_email is empty" in n for n in notes)
    k = _kinds(snaps)
    for kind, days in EXPECTED.items():
        assert _remaining_2026(k[kind]) == [_at(d) for d in days]
        assert k[kind].source["publisher"].startswith("Federal Reserve Bank of St. Louis")
    urls = [u for _, _, u in main.calls if "fred" in u]
    assert "https://fred.stlouisfed.org/releases/calendar?rid=10&y=2026" in urls
    assert "https://fred.stlouisfed.org/releases/calendar?rid=46&y=2026" in urls
    assert "https://fred.stlouisfed.org/releases/calendar?rid=50&y=2026" in urls
    assert any("y=2027" in u for u in urls)                   # October: next year's page is tried too


def test_email_set_uses_bls_ics_only():
    main, bls = Recorder(), Recorder()
    snaps, notes = LiveMacroCalendars(main, "https://fed/fomc", bls_fetcher=bls).get_calendars(FETCHED)
    assert [c[1] for c in bls.calls] == ["bls-ics"]           # ONE request to bls.gov (BLS rate policy)
    assert not any("fred" in c[1] for c in main.calls)
    k = _kinds(snaps)
    for kind, days in EXPECTED.items():
        assert _remaining_2026(k[kind]) == [_at(d) for d in days] and k[kind].source["id"] == "src-bls-ics"


def test_ics_missing_a_kind_falls_back_only_for_that_kind():
    no_ppi = ICS.replace("SUMMARY:Producer Price Index", "SUMMARY:Producer Price Index (draft)")
    main, bls = Recorder(), Recorder(ics=no_ppi)
    snaps, _ = LiveMacroCalendars(main, "https://fed/fomc", bls_fetcher=bls).get_calendars(FETCHED)
    assert {c[1] for c in main.calls if c[1].startswith("fred-")} == {"fred-ppi-2026", "fred-ppi-2027"}
    k = _kinds(snaps)
    assert k["PPI"].source["id"] == "src-fred-rid46" and k["CPI"].source["id"] == "src-bls-ics"
    assert _remaining_2026(k["PPI"]) == [_at(d) for d in EXPECTED["PPI"]]


def test_everything_down_leaves_bls_kinds_missing_with_notes():
    main, bls = Recorder(blocked=("fred",)), Recorder(blocked=("bls",))
    snaps, notes = LiveMacroCalendars(main, "https://fed/fomc", bls_fetcher=bls).get_calendars(FETCHED)
    assert [s.kind for s in snaps] == ["FOMC"]
    assert sum(n.startswith("FRED ") for n in notes) == 6 and any("bls.ics unavailable" in n for n in notes)
