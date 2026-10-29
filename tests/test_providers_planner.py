"""Parsing of the saved Finviz/Nasdaq responses and the vertical builder (fixtures only, no network)."""
from datetime import date
from pathlib import Path

import pytest

from optpipe.providers.finviz import parse_screener
from optpipe.providers.nasdaq import parse_chain, parse_info

FX = Path(__file__).parent / "fixtures" / "2026-10-13"


def test_finviz_screen_parses():
    r = parse_screener((FX / "fv_toplosers.html").read_text(), "ta_toplosers")
    assert r.status == "ok" and r.rows
    assert r.rows[0].ticker == "SNDX" and r.rows[0].change_pct == pytest.approx(-10.51)
    assert all(row.ticker.isupper() for row in r.rows)


def test_finviz_garbage_is_flagged_not_crashing():
    r = parse_screener("<html><body>blocked</body></html>", "ta_toplosers")
    assert r.status != "ok" and not r.rows


def test_nasdaq_info_premarket_quote():
    q, _ = parse_info((FX / "info_JPM.json").read_text())
    assert q.symbol == "JPM" and q.market_status == "Pre-Market"
    assert q.quote_time.isoformat() == "2026-10-13T07:52:00-04:00"
    assert q.prev_close == 334.3


def test_nasdaq_chain_is_prior_close():
    q, _ = parse_info((FX / "info_JPM.json").read_text())
    c = parse_chain((FX / "oc_JPM.json").read_text(), "JPM", q)
    assert c.quote_basis == "prior_close"           # pre-market chain = previous close, R01 must catch it
    k340 = next(x for x in c.quotes if x.strike == 340 and x.expiry == date(2026, 10, 30) and x.right == "C")
    assert (k340.bid, k340.ask, k340.open_interest) == (5.2, 5.65, 472)
    assert k340.iv is None                           # Nasdaq gives no IV -> R10 FLAG/UNKNOWN downstream


def test_planner_jpm_bull_call(rec):
    t = next(t for t in rec["tickets"] if t["ticket_id"] == "tkt-jpm-bcs-340-350")
    assert [(l["side"], l["type"], l["strike"]) for l in t["legs"]] == [("buy", "call", 340), ("sell", "call", 350)]
    assert t["quantity"] == 1 and t["limit_price"]["amount"] == 3.10
    assert t["max_loss"]["amount_usd"] == 310 and t["max_loss"]["amount_usd"] <= 500
    assert t["hold_window"]["start"]["ny"] < t["hold_window"]["end"]["ny"]
