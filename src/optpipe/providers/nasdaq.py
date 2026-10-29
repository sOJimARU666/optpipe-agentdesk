"""Nasdaq public API provider (quotes, option chain, earnings-date notification).

Known limits (Doodle): chain has no IV; pre-market the chain shows PRIOR-SESSION close quotes;
the default chain response only covers near-the-money strikes and the nearest expiries.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from ..models.common import (Event, EventKind, EventTiming, OptionChain, OptionQuote, QuoteBasis,
                             Right, UnderlyingQuote)
from .base import DataUnavailable, Fetcher

NY = ZoneInfo("America/New_York")
INFO_URL = "https://api.nasdaq.com/api/quote/{sym}/info?assetclass=stocks"
CHAIN_URL = "https://api.nasdaq.com/api/quote/{sym}/option-chain?assetclass=stocks"
_NOT_OPEN = {"pre-market", "after-hours", "closed", "market closed"}


def _num(s: Any) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip().replace("$", "").replace(",", "").replace("%", "").replace("+", "")
    if s in ("", "--", "NA", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(s: Any) -> Optional[int]:
    v = _num(s)
    return None if v is None else int(v)


def parse_ny_timestamp(s: Optional[str]) -> Optional[datetime]:
    """'Oct 13, 2026 7:52 AM ET' or 'Closed at Oct 12, 2026 4:00 PM ET' -> aware NY datetime."""
    if not s:
        return None
    m = re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M)", s)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%b %d, %Y %I:%M %p").replace(tzinfo=NY)


def parse_info(raw: str | dict, source: str = "nasdaq") -> tuple[UnderlyingQuote, list[Event]]:
    d = json.loads(raw) if isinstance(raw, str) else raw
    data = d.get("data") or {}
    if not data:
        raise DataUnavailable("nasdaq info: empty data")
    p, s = data.get("primaryData") or {}, data.get("secondaryData") or {}
    lo = hi = None
    rng = ((data.get("keyStats") or {}).get("fiftyTwoWeekHighLow") or {}).get("value")
    if rng and "-" in rng:
        lo, hi = (_num(x) for x in rng.split("-", 1))
    status = data.get("marketStatus")
    # Pre-market: primary = extended-hours quote, secondary = prior regular close.
    prev_close = _num(s.get("lastSalePrice")) if s else None
    prev_close_time = parse_ny_timestamp(s.get("lastTradeTimestamp")) if s else None
    if prev_close is None and p.get("netChange") and _num(p.get("lastSalePrice")) is not None:
        prev_close = round(_num(p["lastSalePrice"]) - (_num(p["netChange"]) or 0), 4)  # type: ignore[operator]
    q = UnderlyingQuote(
        symbol=data.get("symbol", ""), last=_num(p.get("lastSalePrice")), bid=_num(p.get("bidPrice")),
        ask=_num(p.get("askPrice")), prev_close=prev_close, prev_close_time=prev_close_time,
        pct_change=_num(p.get("percentageChange")), volume=_int(p.get("volume")),
        quote_time=parse_ny_timestamp(p.get("lastTradeTimestamp")), is_realtime=p.get("isRealTime"),
        market_status=status, week52_low=lo, week52_high=hi, source=source,
    )
    events: list[Event] = []
    for n in data.get("notifications") or []:
        for et in n.get("eventTypes") or []:
            m = re.search(r"Earnings Date\s*:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", et.get("message", ""))
            if m:
                events.append(Event(kind=EventKind.EARNINGS, symbol=q.symbol,
                                    date=datetime.strptime(m.group(1), "%b %d, %Y").date(),
                                    timing=EventTiming.UNKNOWN, description="Earnings (Nasdaq notification)",
                                    source=INFO_URL.format(sym=q.symbol)))
    return q, events


def parse_chain(raw: str | dict, symbol: str, quote: Optional[UnderlyingQuote] = None,
                fetched_at: Optional[datetime] = None) -> OptionChain:
    d = json.loads(raw) if isinstance(raw, str) else raw
    data = d.get("data") or {}
    rows = ((data.get("table") or {}).get("rows")) or []
    if not rows:
        raise DataUnavailable("nasdaq chain: no rows")
    # Quote timing: Nasdaq gives no per-contract timestamp. Outside regular hours the chain is the
    # prior session close; during the session we assume ~now (delay unverified - open question).
    status = (quote.market_status or "").lower() if quote else ""
    if quote and status in _NOT_OPEN:
        basis, qt = QuoteBasis.PRIOR_CLOSE, quote.prev_close_time
    elif quote and status:
        basis, qt = QuoteBasis.REALTIME, fetched_at
    else:
        basis, qt = QuoteBasis.UNKNOWN, None
    expiry: Optional[date] = None
    quotes, expiries = [], []
    for r in rows:
        if r.get("expirygroup"):
            expiry = datetime.strptime(r["expirygroup"], "%B %d, %Y").date()
            expiries.append(expiry)
            continue
        strike = _num(r.get("strike"))
        if expiry is None or strike is None:
            continue
        for right, pre in ((Right.CALL, "c_"), (Right.PUT, "p_")):
            quotes.append(OptionQuote(
                underlying=symbol, expiry=expiry, strike=strike, right=right,
                bid=_num(r.get(pre + "Bid")), ask=_num(r.get(pre + "Ask")), last=_num(r.get(pre + "Last")),
                volume=_int(r.get(pre + "Volume")), open_interest=_int(r.get(pre + "Openinterest")),
                iv=None, quote_time=qt, quote_basis=basis))
    return OptionChain(underlying=symbol, expiries=expiries, quotes=quotes, quote_basis=basis,
                       note=data.get("lastTrade"), source=CHAIN_URL.format(sym=symbol), fetched_at=fetched_at)


class NasdaqProvider:
    """Implements QuoteProvider, OptionChainProvider and EventProvider over any Fetcher."""

    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self._info: dict[str, tuple[UnderlyingQuote, list[Event]]] = {}

    def _load_info(self, symbol: str):
        if symbol not in self._info:
            res = self.fetcher.get("nasdaq_info", symbol, INFO_URL.format(sym=symbol))
            q, ev = parse_info(res.text, source=res.url)
            self._info[symbol] = (q.model_copy(update={"fetched_at": res.fetched_at}), ev)
        return self._info[symbol]

    def get_quote(self, symbol: str) -> UnderlyingQuote:
        return self._load_info(symbol)[0]

    def get_events(self, symbol: str) -> list[Event]:
        return self._load_info(symbol)[1]

    def get_chain(self, symbol: str, quote: Optional[UnderlyingQuote] = None) -> OptionChain:
        res = self.fetcher.get("nasdaq_chain", symbol, CHAIN_URL.format(sym=symbol))
        return parse_chain(res.text, symbol, quote, res.fetched_at)
