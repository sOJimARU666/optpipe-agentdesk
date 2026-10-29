"""Typed Risk Officer input ("review input"), shape defined by Doodle's risk_rules.yaml `fixture`.

Every sourced datum is an RField {value, status, as_of}. Owner: Doodle (shape) / Poodle (code).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RField(BaseModel):
    model_config = ConfigDict(extra="ignore")      # tolerate source_id/note/method from session Fields
    value: Any = None
    status: Literal["ok", "stale", "missing", "conflicting"] = "missing"
    as_of: Optional[AwareDatetime] = None

    @property
    def usable(self) -> bool:
        return self.status in ("ok", "stale") and self.value is not None


MISSING = RField()


class RUnderlying(_M):
    symbol: str
    last: RField = MISSING


class RLimit(_M):
    amount: Optional[float] = None
    price_type: Literal["debit", "credit"] = "debit"
    valid_only_if: Optional[str] = None


class RTicket(_M):
    underlying: str
    strategy: Optional[str] = None
    quantity: int
    limit_price: RLimit
    hold_window_start: AwareDatetime
    hold_window_end: AwareDatetime
    intended_event_ids: list[str] = Field(default_factory=list)


class RLeg(_M):
    leg_id: str
    type: Literal["call", "put"]
    side: Literal["buy", "sell"]
    strike: float
    expiry: date
    ratio: int = 1
    bid: RField = MISSING
    ask: RField = MISSING
    open_interest: RField = MISSING


class REvent(_M):
    event_id: str
    kind: str
    underlying: Optional[str] = None
    timing: str = "unknown"
    scheduled_at: RField = MISSING
    dividend_amount: Optional[RField] = None


class RCoverage(_M):
    kind: str
    underlying: Optional[str] = None
    checked_through: RField = MISSING


class RIV(_M):
    iv30_current: RField = MISSING
    iv30_history: RField = MISSING


class ReviewInput(_M):
    evaluated_at: AwareDatetime
    underlying: RUnderlying
    ticket: RTicket
    legs: list[RLeg]
    events: list[REvent] = Field(default_factory=list)
    coverage: list[RCoverage] = Field(default_factory=list)
    iv: RIV = Field(default_factory=RIV)
    open_positions: RField = MISSING
