"""Macro calendars (Fed FOMC page, BLS schedules), Treasury risk-free rate, derived IV / IV30 history,
read-through cache, and R08 / R10 driven by them. All offline: real captures under tests/fixtures/sources/."""
from __future__ import annotations

import copy
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from optpipe.providers.base import DataUnavailable, FetchResult
from optpipe.providers.cache import CachedFetcher
from optpipe.providers.macro import (LiveMacroCalendars, ReplayMacroCalendars, bls_snapshot, fomc_snapshot,
                                     parse_bls, parse_fomc, visible)
from optpipe.providers.treasury import ReplayTreasuryRates, latest_rate, to_continuous
from optpipe.research.agent import ResearchAgent
from optpipe.research.iv import (ExpiryIv, IvHistoryStore, atm_iv, bs_price, implied_vol, interpolate_iv30)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tests" / "fixtures" / "sources"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "2026-10-13"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS = {"CPI": ("bls_cpi.md", "https://www.bls.gov/schedule/news_release/cpi.htm"),
       "PPI": ("bls_ppi.md", "https://www.bls.gov/schedule/news_release/ppi.htm"),
       "NFP": ("bls_empsit.md", "https://www.bls.gov/schedule/news_release/empsit.htm")}
CAPTURED = datetime.fromisoformat("2026-10-29T10:00:00-04:00")      # 15:00 Europe/Berlin, capture day


# ---------------------------------------------------------------- parsers on real captures
def test_parse_fomc_real_capture():
    meetings, last_year = parse_fomc((SRC / "fed_fomccalendars.htm").read_text())
    y2026 = [d for d, _ in meetings if d.year == 2026]
    assert y2026 == [date(2026, m, d) for m, d in [(1, 28), (3, 18), (4, 29), (6, 17), (7, 29), (9, 16),
                                                     (10, 28), (12, 9)]]
    assert dict(meetings)[date(2026, 12, 9)] is True          # '*' = Summary of Economic Projections
    assert last_year == 2027


def test_parse_fomc_spans_and_skips():
    html = """<div class="panel panel-default"><div class="panel-heading"><h4>2030 FOMC Meetings</h4></div>
      <div class="fomc-meeting"><div class="fomc-meeting__month">Apr/May</div><div class="fomc-meeting__date">30-1</div></div>
      <div class="fomc-meeting"><div class="fomc-meeting__month">June</div><div class="fomc-meeting__date">11-12*</div></div>
      <div class="fomc-meeting"><div class="fomc-meeting__month">July</div><div class="fomc-meeting__date">22 (notation vote)</div></div>
      <div class="fomc-meeting"><div class="fomc-meeting__month">Aug</div><div class="fomc-meeting__date">(unscheduled)</div></div>
    </div>"""
    assert parse_fomc(html) == ([(date(2030, 5, 1), False), (date(2030, 6, 12), True)], 2030)


def test_parse_bls_markdown_captures():
    cpi = parse_bls((SRC / "bls_cpi.md").read_text())
    assert cpi[0] == ("October 2026", datetime.fromisoformat("2026-11-10T08:30:00-05:00"))
    nfp = parse_bls((SRC / "bls_empsit.md").read_text())
    assert [a.date() for _, a in nfp[:2]] == [date(2026, 11, 6), date(2026, 12, 4)]
    assert all(a.tzinfo is not None and a.utcoffset() == timedelta(hours=-5) for _, a in nfp[:2])


def test_parse_bls_html_table():
    html = """<table><tr><th>Reference Month</th><th>Release Date</th><th>Release Time</th></tr>
      <tr><td>March 2027</td><td>Apr. 09, 2027</td><td>08:30 AM</td></tr>
      <tr><td>bad</td><td>n/a</td><td>08:30 AM</td></tr>
      <tr><td>Sept 2027</td><td>Oct. 14, 2027</td><td>10:00 AM</td></tr></table>"""
    got = parse_bls(html)
    assert [r for r, _ in got] == ["March 2027", "Sept 2027"]
    assert got[0][1].isoformat() == "2027-04-09T08:30:00-04:00" and got[1][1].hour == 10


def test_bls_snapshot_drops_past_and_derives_coverage():
    s = bls_snapshot("CPI", (SRC / "bls_cpi.md").read_text(), BLS["CPI"][1],
                     datetime.fromisoformat("2026-11-11T09:00:00-05:00"))
    assert s.events[0].at.date() == date(2026, 12, 10)                 # 11-10 is in the past at fetch time
    assert s.checked_through == date(2027, 12, 10) and s.source["id"] == "src-bls-cpi-schedule"
    with pytest.raises(DataUnavailable):
        bls_snapshot("CPI", "<html>Access Denied</html>", BLS["CPI"][1], CAPTURED)


# ---------------------------------------------------------------- replay + point in time
def test_replay_is_labelled_hand_made():
    snaps, notes = ReplayMacroCalendars(FIXTURE_DIR / "macro_calendars.yaml").get_calendars(
        datetime.fromisoformat("2026-10-13T07:55:00-04:00"))
    assert {s.kind for s in snaps} == {"FOMC", "CPI", "PPI", "NFP"}
    assert all("HAND-MADE" in s.source["notes"] for s in snaps) and "HAND-MADE" in notes[0]
    cpi = next(s for s in snaps if s.kind == "CPI")
    assert cpi.events[0].at.isoformat() == "2026-10-14T08:30:00-04:00"   # extra event (pre-capture)
    nfp = next(s for s in snaps if s.kind == "NFP")
    assert any("2026-11-06" in n for n in nfp.notes)


def test_calendar_fetched_after_as_of_is_invisible():
    snaps, _ = ReplayMacroCalendars(FIXTURE_DIR / "macro_calendars.yaml").get_calendars(CAPTURED)
    keep, notes = visible(snaps, datetime.fromisoformat("2026-10-13T07:00:00-04:00"))
    assert keep == [] and len(notes) == 4


class FakeFetcher:
    """Serves real captures; bls.gov answers 403 like it does to scripted GETs."""

    def __init__(self, blocked=("bls",), at=CAPTURED):
        self.blocked, self.at, self.calls = blocked, at, 0

    def get(self, resource, key, url):
        self.calls += 1
        if any(b in key for b in self.blocked):
            raise DataUnavailable(f"HTTP 403 for {url}")
        if key == "fomc":
            return FetchResult((SRC / "fed_fomccalendars.htm").read_text(), url, self.at, "live")
        kind = key.removeprefix("bls-").upper()
        return FetchResult((SRC / BLS[kind][0]).read_text(), url, self.at, "live")


def test_live_calendars_survive_bls_403_and_fall_back_to_fred():
    """BLS ICS answers 403 -> every BLS kind comes from FRED; nothing is lost."""
    fred_pages = {10: "fred_calendar_rid10_2026.html", 46: "fred_calendar_rid46_2026.html",
                  50: "fred_calendar_rid50_2026.html"}

    class Fred(FakeFetcher):
        def get(self, resource, key, url):
            if key.startswith("fred-"):
                self.calls += 1
                rid = int(url.split("rid=")[1].split("&")[0])
                if "y=2026" not in url:
                    raise DataUnavailable(f"HTTP 404 for {url}")
                return FetchResult((SRC / fred_pages[rid]).read_text(), url, self.at, "live")
            return super().get(resource, key, url)

    p = LiveMacroCalendars(Fred(blocked=()), FOMC_URL, bls_fetcher=FakeFetcher(blocked=("bls",)))
    snaps, notes = p.get_calendars(CAPTURED)
    assert [s.kind for s in snaps] == ["FOMC", "CPI", "PPI", "NFP"]
    assert any("bls.ics unavailable" in n and "403" in n for n in notes)
    assert all(s.source["id"].startswith("src-fred-") for s in snaps[1:])


# ---------------------------------------------------------------- cache
def test_cache_hit_miss_and_stale_fallback(tmp_path):
    inner = FakeFetcher(blocked=())
    t0 = datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc)
    c = CachedFetcher(inner, tmp_path, ttl_hours=12, now=t0)
    r1 = c.get("macro", "fomc", FOMC_URL)
    assert c.last_status["fomc"] == "miss" and inner.calls == 1
    r2 = c.get("macro", "fomc", FOMC_URL)
    assert c.last_status["fomc"] == "hit" and inner.calls == 1 and r2.kind == "cache" and r2.text == r1.text
    inner.blocked = ("fomc",)
    c2 = CachedFetcher(inner, tmp_path, ttl_hours=12, now=t0 + timedelta(days=2))
    r3 = c2.get("macro", "fomc", FOMC_URL)
    assert c2.last_status["fomc"] == "stale-fallback" and r3.fetched_at == CAPTURED   # true age kept
    with pytest.raises(DataUnavailable):
        CachedFetcher(inner, tmp_path / "empty", 12, now=t0).get("macro", "fomc", FOMC_URL)


# ---------------------------------------------------------------- Treasury rate
def test_treasury_rate_from_real_csv():
    text = (SRC / "treasury_bill_rates_2026.csv").read_text()
    rp = latest_rate(text, date(2026, 10, 13), "13 WEEKS COUPON EQUIVALENT", {"id": "src-treasury-bill-rates"})
    assert rp.observed == date(2026, 10, 9) and rp.yield_pct == 4.16      # 10-12 Columbus Day: no row
    assert rp.rate == pytest.approx(math.log(1 + 0.0416 * 91 / 365) / (91 / 365))
    assert round(rp.rate, 6) == 0.041386
    assert rp.as_of.isoformat() == "2026-10-09T17:00:00-04:00"


def test_treasury_rate_uses_rows_strictly_before_run_date():
    text = (SRC / "treasury_bill_rates_2026.csv").read_text()
    a = latest_rate(text, date(2026, 10, 14), "13 WEEKS COUPON EQUIVALENT", {})
    assert a.observed == date(2026, 10, 13)
    with pytest.raises(DataUnavailable):
        latest_rate(text, date(2026, 1, 2), "13 WEEKS COUPON EQUIVALENT", {})


def test_replay_rate_point_in_time():
    r = ReplayTreasuryRates(FIXTURE_DIR / "macro_calendars.yaml", "13 WEEKS COUPON EQUIVALENT")
    assert r.get_rate(datetime.fromisoformat("2026-10-13T07:52:00-04:00")).source["id"] == "src-treasury-bill-rates"
    with pytest.raises(DataUnavailable):
        r.get_rate(datetime.fromisoformat("2026-10-13T07:00:00-04:00"))


def test_to_continuous_small_rate_limit():
    assert to_continuous(0.0, 91) == 0.0 and to_continuous(4.0, 91) < 0.04


# ---------------------------------------------------------------- Black-Scholes IV
@pytest.mark.parametrize("right", ["call", "put"])
@pytest.mark.parametrize("k,sig", [(90, 0.2), (100, 0.35), (115, 0.6)])
def test_implied_vol_round_trip(right, k, sig):
    p = bs_price(right, 100, k, 0.25, 0.04, sig)
    assert implied_vol(right, p, 100, k, 0.25, 0.04) == pytest.approx(sig, abs=1e-5)


def test_right_spellings():
    assert bs_price("C", 100, 100, 0.25, 0.04, 0.3) == bs_price("call", 100, 100, 0.25, 0.04, 0.3)
    with pytest.raises(ValueError):
        bs_price("X", 100, 100, 0.25, 0.04, 0.3)


def test_put_call_parity():
    s, k, t, r, v = 345, 340, 0.05, 0.041, 0.25
    assert bs_price("call", s, k, t, r, v) - bs_price("put", s, k, t, r, v) == pytest.approx(
        s - k * math.exp(-r * t))


def test_implied_vol_none_outside_no_arbitrage_band():
    assert implied_vol("call", 0.5, 100, 80, 0.25, 0.04) is None      # below intrinsic
    assert implied_vol("call", 150, 100, 80, 0.25, 0.04) is None      # above the underlying
    assert implied_vol("call", 0, 100, 100, 0.25, 0.04) is None
    assert implied_vol("call", 5, 100, 100, 0, 0.04) is None


def test_atm_iv_linear_in_strike_with_call_put_mean():
    pts = [(340, "call", 0.30), (340, "put", 0.32), (350, "call", 0.26), (350, "put", 0.28)]
    assert atm_iv(345, pts) == pytest.approx(0.29)
    assert atm_iv(360, pts) == pytest.approx(0.27)                      # nearest strike outside the range


def test_interpolate_iv30_total_variance():
    a, b = ExpiryIv(date(2026, 11, 1), 20 / 365, 0.20), ExpiryIv(date(2026, 11, 21), 40 / 365, 0.30)
    v, how = interpolate_iv30([b, a], 30)
    assert v == pytest.approx(math.sqrt((0.04 * 20 + (0.09 * 40 - 0.04 * 20) * 0.5) / 30))
    assert "total-variance" in how
    v2, how2 = interpolate_iv30([b], 30)
    assert v2 == 0.30 and "flat extrapolation" in how2
    assert interpolate_iv30([], 30)[0] is None


# ---------------------------------------------------------------- IV history store
def test_iv_history_point_in_time_and_dedup(tmp_path):
    st = IvHistoryStore(tmp_path)
    t = lambda s: datetime.fromisoformat(s)                            # noqa: E731
    st.record("jpm", date(2026, 10, 9), 0.25, t("2026-10-09T17:00:00-04:00"), "run-a", "fixtures")
    st.record("JPM", date(2026, 10, 9), 0.25, t("2026-10-09T17:00:00-04:00"), "run-a", "fixtures")   # dup
    st.record("JPM", date(2026, 10, 9), 0.26, t("2026-10-09T18:00:00-04:00"), "run-b", "fixtures")   # later run
    st.record("JPM", date(2026, 10, 12), 0.27, t("2026-10-13T08:00:00-04:00"), "run-c", "fixtures")
    st.record("JPM", date(2026, 9, 1), 0.10, t("2026-09-01T17:00:00-04:00"), "run-d", "fixtures")    # > 30 d
    assert len(st.load("JPM")) == 4
    h = st.history("JPM", date(2026, 10, 12), t("2026-10-13T09:00:00-04:00"))
    assert h == [(date(2026, 10, 9), 0.26)]                             # same-day obs excluded, last per day
    h_early = st.history("JPM", date(2026, 10, 12), t("2026-10-09T17:30:00-04:00"))
    assert h_early == [(date(2026, 10, 9), 0.25)]                       # run-b not yet recorded


# ---------------------------------------------------------------- R08 with real calendars
def _macro_rows(snaps, as_of):
    inp = SimpleNamespace(as_of=as_of, events=[], source_times={})
    events, _ = ResearchAgent._events(inp, [], {}, snaps)
    return events, ResearchAgent._macro_coverage(snaps, as_of)


def _real_snaps(fetched_at=CAPTURED, kinds=("FOMC", "CPI", "PPI", "NFP")):
    out = []
    for k in kinds:
        if k == "FOMC":
            out.append(fomc_snapshot((SRC / "fed_fomccalendars.htm").read_text(), FOMC_URL, fetched_at))
        else:
            out.append(bls_snapshot(k, (SRC / BLS[k][0]).read_text(), BLS[k][1], fetched_at))
    return out


def _r08(raw_rules, ruleset, snaps, start, end, evaluated="2026-11-16T11:00:00-05:00"):
    from optpipe.risk import evaluate
    inp = copy.deepcopy(raw_rules["fixture"])
    at = datetime.fromisoformat(evaluated)
    keep, _ = visible(snaps, at)
    ev, cov = _macro_rows(keep, at)
    fld = lambda f: {k: f[k] for k in ("value", "source_id", "as_of", "status")}           # noqa: E731
    ev = [{"event_id": e["event_id"], "kind": e["kind"], "underlying": None, "timing": e["timing"],
           "scheduled_at": fld(e["scheduled_at"])} for e in ev]
    cov = [{"kind": c["kind"], "underlying": None, "checked_through": fld(c["checked_through"])} for c in cov]
    inp["evaluated_at"] = evaluated
    inp["ticket"]["hold_window_start"], inp["ticket"]["hold_window_end"] = start, end
    for leg in inp["legs"]:                     # window_end = min(hold end, earliest expiry close): move expiry out
        leg["expiry"] = "2026-12-18"
    inp["events"] = [e for e in inp["events"] if e["underlying"]] + ev
    inp["coverage"] = [c for c in inp["coverage"] if c["underlying"]] + cov
    out = evaluate(inp, ruleset).model_dump(mode="json")
    return next(r for r in out["results"] if r["rule_id"].startswith("R08"))


@pytest.fixture(scope="module")
def ruleset(spec):
    from optpipe.risk import load_ruleset
    return load_ruleset(spec)[0]


def test_r08_pass_with_real_calendars(raw_rules, ruleset):
    # 2026-11-16 .. 12-03: after CPI 11-10 / PPI 11-13 / NFP 11-06, before NFP 12-04 / FOMC 12-09
    r = _r08(raw_rules, ruleset, _real_snaps(), "2026-11-16T09:45:00-05:00", "2026-12-03T16:00:00-05:00")
    assert r["result"] == "PASS" and r["measured"]["events_in_window"] == [], r


def test_r08_flag_on_nfp_in_window(raw_rules, ruleset):
    r = _r08(raw_rules, ruleset, _real_snaps(), "2026-11-16T09:45:00-05:00", "2026-12-04T16:00:00-05:00")
    assert r["result"] == "FLAG" and "NFP" in json.dumps(r)


def test_r08_unknown_when_bls_blocked(raw_rules, ruleset):
    r = _r08(raw_rules, ruleset, _real_snaps(kinds=("FOMC", "CPI", "PPI")),
             "2026-11-16T09:45:00-05:00", "2026-12-03T16:00:00-05:00")
    assert r["result"] == "UNKNOWN"


def test_r08_unknown_when_calendar_fetched_later(raw_rules, ruleset):
    late = _real_snaps(fetched_at=datetime.fromisoformat("2026-11-20T08:00:00-05:00"))
    r = _r08(raw_rules, ruleset, late, "2026-11-16T09:45:00-05:00", "2026-12-03T16:00:00-05:00")
    assert r["result"] == "UNKNOWN"


def test_macro_coverage_capped_to_horizon():
    cov = ResearchAgent._macro_coverage(_real_snaps(), CAPTURED)
    assert {c["kind"] for c in cov} == {"FOMC", "CPI", "PPI", "NFP"}
    assert all(c["checked_through"]["value"] == "2027-02-26" for c in cov)       # 10-29 + 120 d
    assert all(c["checked_through"]["method"] == "derived" and "min(" in c["checked_through"]["formula"]
               for c in cov)


# ---------------------------------------------------------------- pipeline: derived IV + R10
def _seed(store_dir: Path, symbol: str, n: int, end: date = date(2026, 10, 11)):
    st = IvHistoryStore(store_dir)
    for i in range(n):
        d = end - timedelta(days=i)
        st.record(symbol, d, 0.20 + 0.005 * i, datetime.combine(d, datetime.min.time()).replace(
            tzinfo=timezone(timedelta(hours=-4))) + timedelta(hours=17), f"seed-{i}", "fixtures")


def _run(settings, state: Path, monkeypatch):
    from optpipe.orchestrator import load_inputs, run_session
    monkeypatch.setenv("OPTPIPE_STATE_DIR", str(state))
    inputs = load_inputs(FIXTURE_DIR / "session_inputs.yaml")
    rec, _ = run_session(settings, inputs, inputs.as_of, FIXTURE_DIR)
    return rec


def _r10(rec):
    return {rv["ticket_id"]: next(r for r in rv["results"] if r["rule_id"].startswith("R10"))["result"]
            for rv in rec["risk_reviews"]}


def test_contract_iv_is_derived_with_rate_source(settings, spec, tmp_path, monkeypatch):
    rec = _run(settings, tmp_path, monkeypatch)
    ivs = [k["iv"] for c in rec["candidates"] for sn in c["snapshots"] for s in sn["option_chain_slices"]
           for k in s["contracts"]]
    ok = [v for v in ivs if v["value"] is not None]
    assert ok and all(v["method"] == "derived" and "Black-Scholes" in v["formula"] for v in ok)
    assert all("src-treasury-bill-rates" in v["note"] for v in ok)
    assert any(s["id"] == "src-treasury-bill-rates" for s in rec["sources"])
    assert (tmp_path / "fixtures" / "iv_history" / "JPM.jsonl").exists()


def test_r10_unknown_below_20_points(settings, spec, tmp_path, monkeypatch):
    _seed(tmp_path / "fixtures" / "iv_history", "JPM", 19)
    rec = _run(settings, tmp_path, monkeypatch)
    jpm = next(c for c in rec["candidates"] if c["underlying"] == "JPM")
    assert jpm["snapshots"][0]["iv"]["iv30_history"]["value"] is not None
    assert len(jpm["snapshots"][0]["iv"]["iv30_history"]["value"]) == 19
    jt = [t["ticket_id"] for t in rec["tickets"] if t["underlying"] == "JPM"]
    assert jt and all(_r10(rec)[t] == "UNKNOWN" for t in jt)


def test_r10_decides_with_20_points(settings, spec, tmp_path, monkeypatch):
    _seed(tmp_path / "fixtures" / "iv_history", "JPM", 20)
    rec = _run(settings, tmp_path, monkeypatch)
    jpm = next(c for c in rec["candidates"] if c["underlying"] == "JPM")
    h = jpm["snapshots"][0]["iv"]["iv30_history"]
    assert len(h["value"]) == 20 and h["method"] == "derived" and h["source_id"] == "src-optpipe-iv-history-jpm"
    jt = [t["ticket_id"] for t in rec["tickets"] if t["underlying"] == "JPM"]
    assert jt and all(_r10(rec)[t] in ("PASS", "FLAG") for t in jt)
