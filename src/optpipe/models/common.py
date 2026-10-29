"""Market-data primitives. Owner: Doodle (finance semantics), Poodle (code)."""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "Model", "Right", "Bias", "QuoteBasis", "OptionQuote", "UnderlyingQuote", "OptionChain",
    "EventKind", "EventTiming", "Event", "MissingData", "SourceRef", "IVSnapshot", "ScreenRow", "ScreenResult",
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Right(StrEnum):
    CALL = "C"
    PUT = "P"


class Bias(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class QuoteBasis(StrEnum):
    REALTIME = "realtime"
    PRIOR_CLOSE = "prior_close"        # e.g. Nasdaq chain fetched pre-market
    UNKNOWN = "unknown"


class OptionQuote(Model):
    underlying: str
    expiry: date
    strike: float
    right: Right
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    iv: Optional[float] = None
    quote_time: Optional[datetime] = None
    quote_basis: QuoteBasis = QuoteBasis.UNKNOWN

    @computed_field  # type: ignore[misc]
    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None or self.ask <= 0:
            return None
        return round((self.bid + self.ask) / 2, 4)

    @computed_field  # type: ignore[misc]
    @property
    def spread_pct(self) -> Optional[float]:
        m = self.mid
        if m is None or m <= 0:
            return None
        return round((self.ask - self.bid) / m * 100, 2)  # type: ignore[operator]

    @property
    def label(self) -> str:
        k = f"{self.strike:g}"
        return f"{self.underlying} {self.expiry.isoformat()} {k}{self.right.value}"


class UnderlyingQuote(Model):
    symbol: str
    last: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    prev_close: Optional[float] = None
    prev_close_time: Optional[datetime] = None
    pct_change: Optional[float] = None
    volume: Optional[int] = None
    avg_volume: Optional[int] = None
    quote_time: Optional[datetime] = None
    is_realtime: Optional[bool] = None
    market_status: Optional[str] = None
    week52_low: Optional[float] = None
    week52_high: Optional[float] = None
    source: Optional[str] = None
    fetched_at: Optional[datetime] = None


class OptionChain(Model):
    underlying: str
    expiries: list[date] = Field(default_factory=list)
    quotes: list[OptionQuote] = Field(default_factory=list)
    quote_basis: QuoteBasis = QuoteBasis.UNKNOWN
    note: Optional[str] = None
    source: Optional[str] = None
    fetched_at: Optional[datetime] = None

    def find(self, expiry: date, strike: float, right: Right) -> Optional[OptionQuote]:
        for q in self.quotes:
            if q.expiry == expiry and abs(q.strike - strike) < 1e-9 and q.right == right:
                return q
        return None

    def strikes(self, expiry: date, right: Right) -> list[float]:
        return sorted({q.strike for q in self.quotes if q.expiry == expiry and q.right == right})


class EventKind(StrEnum):
    EARNINGS = "earnings"
    MACRO = "macro"
    EX_DIVIDEND = "ex_dividend"
    OTHER = "other"


class EventTiming(StrEnum):
    BMO = "bmo"          # before market open
    AMC = "amc"          # after market close
    INTRADAY = "intraday"
    UNKNOWN = "unknown"


class Event(Model):
    kind: EventKind
    date: date
    symbol: Optional[str] = None      # None = market-wide (CPI, FOMC...)
    timing: EventTiming = EventTiming.UNKNOWN
    description: str = ""
    source: Optional[str] = None


class IVSnapshot(Model):
    current_iv: Optional[float] = None
    recent_low: Optional[float] = None
    recent_high: Optional[float] = None
    source: Optional[str] = None

    @property
    def percentile(self) -> Optional[float]:
        if None in (self.current_iv, self.recent_low, self.recent_high):
            return None
        rng = self.recent_high - self.recent_low  # type: ignore[operator]
        if rng <= 0:
            return None
        return round((self.current_iv - self.recent_low) / rng * 100, 1)  # type: ignore[operator]


class MissingData(Model):
    stage: str
    item: str
    symbol: Optional[str] = None
    reason: str = ""


class SourceRef(Model):
    label: str
    url: Optional[str] = None
    fetched_at: Optional[datetime] = None
    kind: Literal["live", "fixture", "manual"] = "fixture"


class ScreenRow(Model):
    ticker: str
    company: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[str] = None
    price: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[int] = None


class ScreenResult(Model):
    screen: str
    status: Literal["ok", "empty", "error"]
    rows: list[ScreenRow] = Field(default_factory=list)
    source: Optional[str] = None
    fetched_at: Optional[datetime] = None
    note: Optional[str] = None
