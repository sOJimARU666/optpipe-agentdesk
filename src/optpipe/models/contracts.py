"""Stage input/output contracts: the hand-off boundaries between agents.

Discovery -> Research -> Planner -> RiskOfficer -> Watcher
Owner: Moodle (boundaries). Stage outputs are sections of the v1.1 superset of Doodle's
session.schema.json v1.0.0 (typed by models/schema_v1_1.py; records/compat.to_v1 writes strict v1), so the orchestrator assembles the
session record from stage outputs without re-mapping.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import AwareDatetime, Field

from .common import Bias, Model
# Runtime contracts use the PINNED spec schema (1.1.2); schema_v1_1 (the spec_patches proposal) is tooling only.
from .schema_spec import (Candidate, EventCalendar, GlobalSnapshot, IdeaLogItem, PremarketScan, RiskReview,
                          SessionRecordSpec, Source, Ticket, WatcherRule)

HYPOTHETICAL_LABEL = "HYPOTHETICAL - paper only, not an order"   # schema const on every ticket


# ---------------- inputs supplied by the user / Doodle (no automated source yet) ----------------
class ManualSource(Model):
    id: str
    url: str
    kind: str
    publisher: Optional[str] = None
    title: Optional[str] = None
    fetched_at: Optional[AwareDatetime] = None       # default: session as_of


class Catalyst(Model):
    text: str
    source_id: str
    status: str = "ok"


class ManualCandidate(Model):
    symbol: str
    bias: Bias = Bias.NEUTRAL
    company_name: Optional[str] = None
    exchange: Optional[str] = None
    catalysts: list[Catalyst] = Field(default_factory=list)


class ManualEvent(Model):
    event_id: str
    kind: str                                        # schema EventKind
    underlying: Optional[str] = None
    timing: str = "unknown"
    scheduled_at: AwareDatetime
    source_id: str
    as_of: Optional[AwareDatetime] = None
    dividend_amount: Optional[float] = None
    description: str = ""
    note: Optional[str] = None


class ManualCoverage(Model):
    kind: str
    underlying: Optional[str] = None
    checked_through: Optional[str] = None            # YYYY-MM-DD; None => missing (rule sees UNKNOWN)
    source_id: Optional[str] = None
    formula: Optional[str] = None
    note: Optional[str] = None


# ---------------- Discovery ----------------
class Pick(Model):
    """Internal: why a symbol was picked (feeds Research)."""
    symbol: str
    bias: Bias = Bias.NEUTRAL
    company_name: Optional[str] = None
    exchange: Optional[str] = None
    catalysts: list[Catalyst] = Field(default_factory=list)
    screens: list[str] = Field(default_factory=list)


class DiscoveryInput(Model):
    as_of: AwareDatetime
    run_id: str
    screens: list[str]
    watchlist: list[str] = Field(default_factory=list)
    manual_candidates: list[ManualCandidate] = Field(default_factory=list)
    max_candidates: int = 4


class DiscoveryOutput(Model):
    premarket_scan: PremarketScan
    global_snapshot: GlobalSnapshot
    sources: list[Source] = Field(default_factory=list)
    picks: list[Pick] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------- Research ----------------
class ResearchInput(Model):
    as_of: AwareDatetime
    run_id: str
    picks: list[Pick]
    events: list[ManualEvent] = Field(default_factory=list)
    coverage: list[ManualCoverage] = Field(default_factory=list)
    source_times: dict[str, AwareDatetime] = Field(default_factory=dict)   # source_id -> fetched_at
    strike_window_pct: float = 10.0


class ResearchOutput(Model):
    candidates: list[Candidate] = Field(default_factory=list)
    event_calendar: EventCalendar
    sources: list[Source] = Field(default_factory=list)
    bias: dict[str, Bias] = Field(default_factory=dict)          # candidate_id -> bias
    notes: list[str] = Field(default_factory=list)


# ---------------- Planner ----------------
class PlannerParams(Model):
    max_risk_per_trade_usd: float
    risk_basis: str | None = None    # where max_risk_per_trade_usd came from (shown on each ticket)
    preferred_dte_min: int = 14
    preferred_dte_max: int = 45
    strike_step: float = 5
    spread_width: float = 10
    stop_fraction_of_debit: float = 0.5
    target_multiple_of_debit: float = 2.0
    entry_slippage_pct: float = 2.5
    time_stop_days_before_expiry: int = 3
    entry_not_before: str = "09:45"
    max_leg_spread_pct: float = 10.0
    penny_pilot: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM", "JPM"])   # fallback only
    tick_threshold: float = 3.0                                                         # fallback only
    tick_rules: Optional[dict] = None     # Doodle's risk_rules.yaml config.tick_rules (wins when present)


class PlannerInput(Model):
    as_of: AwareDatetime
    run_id: str
    candidates: list[Candidate]
    event_calendar: EventCalendar
    bias: dict[str, Bias] = Field(default_factory=dict)
    params: PlannerParams


class PlannerOutput(Model):
    tickets: list[Ticket] = Field(default_factory=list)
    idea_log: list[IdeaLogItem] = Field(default_factory=list)   # incl. rejected ideas and NO TRADE
    notes: list[str] = Field(default_factory=list)


# ---------------- Risk Officer ----------------
class RiskInput(Model):
    """The draft record (everything up to tickets) plus when/for which run to review."""
    record: SessionRecordSpec
    run_id: str
    evaluated_at: AwareDatetime


class RiskOutput(Model):
    risk_reviews: list[RiskReview] = Field(default_factory=list)


# ---------------- Watcher (defined rules only; no monitoring loop yet) ----------------
class WatcherInput(Model):
    tickets: list[Ticket] = Field(default_factory=list)
    risk_reviews: list[RiskReview] = Field(default_factory=list)


class WatcherOutput(Model):
    watcher_rules: list[WatcherRule] = Field(default_factory=list)
    note: str = "Rules defined only, inactive until a user approval exists; no monitoring loop yet (stub)."
