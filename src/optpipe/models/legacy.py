"""Model for Doodle's hand-written v0 session record (e.g. session-2026-10-13.json).

The v0 file mixes structured values and free text, so the sections that are free-form are typed
loosely. Purpose: lossless read/write of historical records and a golden reference for tests.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class LegacySessionMeta(_Open):
    date: str
    mode: str
    created_ny: str
    created_berlin: str
    us_open_ny: str
    us_open_berlin: str
    market_status_at_fetch: Optional[str] = None


class LegacyOrderTicket(_Open):
    label: str
    underlying: str
    structure: str
    expiry: str
    dte_from_today: int
    quantity: int
    reference_mid_debit: float
    natural_debit: float
    breakeven: float
    alternatives: list[str] = Field(default_factory=list)


class LegacyRiskOfficer(_Open):
    rules: dict[str, str]
    overall: str


class LegacyIdea(_Open):
    idea: str
    status: str


class LegacySessionRecord(_Open):
    session: LegacySessionMeta
    discovery: dict[str, Any]
    candidates: dict[str, dict[str, Any]]
    order_ticket: LegacyOrderTicket
    risk_officer: LegacyRiskOfficer
    paper_position: dict[str, Any]
    idea_log: list[LegacyIdea]
    missing_data: list[str]

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_unset=True)
