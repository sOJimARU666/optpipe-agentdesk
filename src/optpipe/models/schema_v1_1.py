"""GENERATED from the v1.1 PROPOSAL spec_patches/v1.1/session.schema.json - do not edit by hand.

Regenerate: .venv/bin/python scripts/gen_models.py
"""
from __future__ import annotations

SCHEMA_SHA256 = "3c6dafbf71b2efc465608294ffc7741fb286c4c50388df323516dec75a2a67f5"
SCHEMA_SOURCE = "spec_patches/v1.1/session.schema.json"

from datetime import date as date_aliased
from enum import Enum, IntEnum
from typing import Any, Literal

Number = int | float  # JSON Schema 'number', kept lossless

from datetime import datetime as _dt
from typing import Annotated
from pydantic import AfterValidator


def _aware(v: str) -> str:
    if _dt.fromisoformat(v).tzinfo is None:
        raise ValueError(f'timestamp needs an explicit UTC offset: {v}')
    return v


AwareTs = Annotated[str, AfterValidator(_aware)]  # offset-aware ISO-8601, stored verbatim

from pydantic import BaseModel, ConfigDict, Field, RootModel


class SchemaVersion(Enum):
    field_1_0_0 = '1.0.0'
    field_1_1_0 = '1.1.0'


class NyBerlinOffsetHours(IntEnum):
    integer_5 = 5
    integer_6 = 6


class Kind(Enum):
    screener = 'screener'
    quote_api = 'quote_api'
    option_chain_api = 'option_chain_api'
    news = 'news'
    filing = 'filing'
    company_ir = 'company_ir'
    economic_calendar = 'economic_calendar'
    exchange_calendar = 'exchange_calendar'
    data_vendor = 'data_vendor'
    user_input = 'user_input'
    derived = 'derived'


class Phase(Enum):
    premarket = 'premarket'
    preopen = 'preopen'
    open = 'open'
    intraday = 'intraday'
    postopen = 'postopen'
    close = 'close'
    afterhours = 'afterhours'
    closed = 'closed'


class Region(Enum):
    asia_pacific = 'asia_pacific'
    europe = 'europe'
    us = 'us'
    other = 'other'


class SessionState(Enum):
    close = 'close'
    intraday = 'intraday'
    early = 'early'
    futures = 'futures'
    unknown = 'unknown'


class Timing(Enum):
    bmo = 'bmo'
    amc = 'amc'
    during_market = 'during_market'
    at_time = 'at_time'
    unknown = 'unknown'


class Strategy(Enum):
    long_call = 'long_call'
    long_put = 'long_put'
    bull_call_debit_spread = 'bull_call_debit_spread'
    bear_put_debit_spread = 'bear_put_debit_spread'
    bull_put_credit_spread = 'bull_put_credit_spread'
    bear_call_credit_spread = 'bear_call_credit_spread'
    iron_condor = 'iron_condor'
    calendar = 'calendar'
    other = 'other'


class Direction(Enum):
    bullish = 'bullish'
    bearish = 'bearish'
    neutral = 'neutral'
    volatility = 'volatility'


class Breakeven(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    underlying_price: Number
    at_price: Number


class Kind1(Enum):
    trade = 'trade'
    no_trade = 'no_trade'


class Decision(Enum):
    selected = 'selected'
    preferred = 'preferred'
    not_selected = 'not_selected'
    rejected = 'rejected'


class Severity(Enum):
    hard = 'hard'
    soft = 'soft'


class Verdict(Enum):
    PASS = 'PASS'
    FLAG = 'FLAG'
    VETO = 'VETO'


class Overall(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    verdict: Verdict = Field(
        ...,
        description='VETO if any rule VETO or any hard rule UNKNOWN; else FLAG if any FLAG or soft UNKNOWN; else PASS',
    )
    blocking_rule_ids: list[str]
    flag_rule_ids: list[str]
    approval_required: Literal[True] = Field(
        ..., description='Always true: even PASS needs explicit contract-level approval'
    )
    summary: str | None = None


class Channel(Enum):
    web_ui = 'web_ui'
    chat = 'chat'
    api = 'api'
    other = 'other'


class Approver(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    name: str = Field(..., min_length=1)
    channel: Channel
    evidence: str | None = Field(
        None, description='message id / UI click log reference'
    )


class Status1(Enum):
    active = 'active'
    expired = 'expired'
    revoked = 'revoked'
    consumed = 'consumed'


class Status2(Enum):
    pending_fill = 'pending_fill'
    open = 'open'
    closed = 'closed'
    cancelled = 'cancelled'


class FillModel(Enum):
    mid = 'mid'
    natural = 'natural'
    limit_if_touched = 'limit_if_touched'


class Action(Enum):
    buy_to_open = 'buy_to_open'
    sell_to_open = 'sell_to_open'
    buy_to_close = 'buy_to_close'
    sell_to_close = 'sell_to_close'


class Reason(Enum):
    target = 'target'
    stop = 'stop'
    time_stop = 'time_stop'
    expiry = 'expiry'
    manual = 'manual'
    assignment_risk = 'assignment_risk'
    other = 'other'


class Kind2(Enum):
    option_value = 'option_value'
    underlying_price = 'underlying_price'
    iv_change = 'iv_change'
    dte = 'dte'
    assignment = 'assignment'
    time = 'time'
    event = 'event'


class Severity1(Enum):
    info = 'info'
    warn = 'warn'
    action = 'action'


class Action1(Enum):
    alert = 'alert'
    suggest_close = 'suggest_close'
    force_close_paper = 'force_close_paper'


class Actor(Enum):
    scanner = 'scanner'
    decision_maker = 'decision_maker'
    risk_officer = 'risk_officer'
    user = 'user'
    simulator = 'simulator'
    watcher = 'watcher'


class DataQuality(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    missing: list[str]
    stale: list[str]
    conflicting: list[str]
    notes: list[str] | None = None


class TraceRef(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    path: str = Field(
        ...,
        description="relative to this record's directory",
        pattern='^[^/].*\\.trace\\.json$',
    )
    sha256: str = Field(..., pattern='^[0-9a-f]{64}$')


class Id(RootModel[str]):
    root: str = Field(..., pattern='^[a-z][a-z0-9_-]{1,63}$')


class SourceId(RootModel[str]):
    root: str = Field(..., pattern='^src-[a-z0-9_-]{1,60}$')


class Ticker(RootModel[str]):
    root: str = Field(..., pattern='^[A-Z][A-Z0-9.]{0,5}$')


class Date(RootModel[date_aliased]):
    root: date_aliased


class Timestamp(RootModel[AwareTs]):
    root: AwareTs = Field(
        ..., description='RFC 3339 with explicit offset (naive timestamps forbidden)'
    )


class TsPair(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ny: AwareTs
    berlin: AwareTs


class OccSymbol(RootModel[str]):
    root: str = Field(
        ...,
        description='OCC symbol without padding, e.g. JPM261030C00340000',
        pattern='^[A-Z][A-Z0-9.]{0,5}\\d{6}[CP]\\d{8}$',
    )


class Status(Enum):
    ok = 'ok'
    stale = 'stale'
    missing = 'missing'
    conflicting = 'conflicting'


class RuleResult(Enum):
    PASS = 'PASS'
    VETO = 'VETO'
    FLAG = 'FLAG'
    UNKNOWN = 'UNKNOWN'


class IdeaStatus(Enum):
    proposed = 'proposed'
    rejected = 'rejected'
    vetoed = 'vetoed'
    approved_pending = 'approved_pending'
    approved = 'approved'
    simulated = 'simulated'
    executed = 'executed'
    closed = 'closed'


class EventKind(Enum):
    earnings = 'earnings'
    ex_dividend = 'ex_dividend'
    CPI = 'CPI'
    PPI = 'PPI'
    FOMC = 'FOMC'
    NFP = 'NFP'
    other_macro = 'other_macro'


class Conflict(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: Any
    source_id: SourceId
    as_of: Timestamp | None
    note: str | None = None


class Method(Enum):
    reported = 'reported'
    derived = 'derived'


class FieldModel(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: Any
    source_id: SourceId | None
    as_of: Timestamp | None = Field(
        ..., description='instant the value refers to (quote time), not fetch time'
    )
    status: Status
    conflicts: list[Conflict] | None = None
    method: Method | None = Method.reported
    formula: str | None = Field(
        None, description='required when method=derived, e.g. (bid+ask)/2'
    )
    unit: str | None = None
    note: str | None = None


class Conflict1(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: Number
    source_id: SourceId
    as_of: Timestamp | None
    note: str | None = None


class NumberField(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: Number | None
    source_id: SourceId | None
    as_of: Timestamp | None = Field(
        ..., description='instant the value refers to (quote time), not fetch time'
    )
    status: Status
    conflicts: list[Conflict1] | None = None
    method: Method | None = Method.reported
    formula: str | None = Field(
        None, description='required when method=derived, e.g. (bid+ask)/2'
    )
    unit: str | None = None
    note: str | None = None


class Conflict2(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: str
    source_id: SourceId
    as_of: Timestamp | None
    note: str | None = None


class StringField(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: str | None
    source_id: SourceId | None
    as_of: Timestamp | None = Field(
        ..., description='instant the value refers to (quote time), not fetch time'
    )
    status: Status
    conflicts: list[Conflict2] | None = None
    method: Method | None = Method.reported
    formula: str | None = Field(
        None, description='required when method=derived, e.g. (bid+ask)/2'
    )
    unit: str | None = None
    note: str | None = None


class Conflict3(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: Any
    source_id: SourceId
    as_of: Timestamp | None
    note: str | None = None


class BoolField(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: bool | None
    source_id: SourceId | None
    as_of: Timestamp | None = Field(
        ..., description='instant the value refers to (quote time), not fetch time'
    )
    status: Status
    conflicts: list[Conflict3] | None = None
    method: Method | None = Method.reported
    formula: str | None = Field(
        None, description='required when method=derived, e.g. (bid+ask)/2'
    )
    unit: str | None = None
    note: str | None = None


class DateField(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: Date | None
    source_id: SourceId | None
    as_of: Timestamp | None = Field(
        ..., description='instant the value refers to (quote time), not fetch time'
    )
    status: Status
    conflicts: list[Conflict3] | None = None
    method: Method | None = Method.reported
    formula: str | None = Field(
        None, description='required when method=derived, e.g. (bid+ask)/2'
    )
    unit: str | None = None
    note: str | None = None


class TimestampField(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: Timestamp | None
    source_id: SourceId | None
    as_of: Timestamp | None = Field(
        ..., description='instant the value refers to (quote time), not fetch time'
    )
    status: Status
    conflicts: list[Conflict3] | None = None
    method: Method | None = Method.reported
    formula: str | None = Field(
        None, description='required when method=derived, e.g. (bid+ask)/2'
    )
    unit: str | None = None
    note: str | None = None


class NumberArrayField(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: list[Number] | None
    source_id: SourceId | None
    as_of: Timestamp | None = Field(
        ..., description='instant the value refers to (quote time), not fetch time'
    )
    status: Status
    conflicts: list[Conflict3] | None = None
    method: Method | None = Method.reported
    formula: str | None = Field(
        None, description='required when method=derived, e.g. (bid+ask)/2'
    )
    unit: str | None = None
    note: str | None = None


class Type(Enum):
    call = 'call'
    put = 'put'


class Style(Enum):
    american = 'american'
    european = 'european'


class Greeks(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    delta: NumberField
    gamma: NumberField
    theta: NumberField
    vega: NumberField
    rho: NumberField | None = None


class Side(Enum):
    buy = 'buy'
    sell = 'sell'


class Leg(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    leg_id: Id
    contract_id: OccSymbol
    type: Type
    side: Side
    strike: Number = Field(..., gt=0.0)
    expiry: Date
    ratio: int = Field(
        ...,
        description='contracts per spread unit; total = ticket.quantity * ratio',
        ge=1,
    )
    multiplier: int | None = 100


class PriceType(Enum):
    debit = 'debit'
    credit = 'credit'


class Rounding(Enum):
    down = 'down'
    up = 'up'
    nearest = 'nearest'
    none = 'none'


class LimitPrice(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    amount: Number = Field(
        ..., description='net per-share price of one spread unit', ge=0.0
    )
    price_type: PriceType
    valid_only_if: str | None = Field(
        None,
        description='human-readable precondition, e.g. re-quoted live after 09:30 ET',
    )
    raw_amount: Number | None = Field(
        None, description='unrounded price before tick rounding', ge=0.0
    )
    tick_size: Number | None = Field(None, gt=0.0)
    rounding: Rounding | None = Field(
        None, description='direction applied to raw_amount (debit limit: down)'
    )


class PayoffAmount(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    amount_usd: Number | None = Field(
        ...,
        description='for whole ticket (quantity included); null only if unlimited',
        ge=0.0,
    )
    unlimited: bool
    at_price: Number = Field(
        ...,
        description='net per-share price the amount assumes (normally limit_price.amount)',
    )
    basis: str


class Metric(Enum):
    underlying_last = 'underlying_last'
    underlying_close = 'underlying_close'
    position_value = 'position_value'
    net_debit_mid = 'net_debit_mid'
    net_credit_mid = 'net_credit_mid'
    leg_spread_pct_of_mid = 'leg_spread_pct_of_mid'
    time_ny = 'time_ny'
    date_ny = 'date_ny'
    dte = 'dte'
    iv_change_pct = 'iv_change_pct'
    short_leg_itm = 'short_leg_itm'
    ex_dividend_in_window = 'ex_dividend_in_window'


class Op(Enum):
    lt = 'lt'
    lte = 'lte'
    gt = 'gt'
    gte = 'gte'
    eq = 'eq'
    cross_above = 'cross_above'
    cross_below = 'cross_below'


class Condition(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    metric: Metric
    op: Op
    value: Number | str | bool
    unit: str | None = None
    raw_value: Number | None = Field(
        None, description='unrounded threshold before tick rounding'
    )
    tick_size: Number | None = Field(None, gt=0.0)
    rounding: Rounding | None = None


class Combine(Enum):
    all = 'all'
    any = 'any'


class TradeRule(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    rule_id: Id
    text: str
    combine: Combine | None = Combine.all
    conditions: list[Condition] = Field(..., min_length=1)


class Conflict7(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: int
    source_id: SourceId
    as_of: Timestamp | None
    note: str | None = None


class IntegerField(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: int | None
    source_id: SourceId | None
    as_of: Timestamp | None = Field(
        ..., description='instant the value refers to (quote time), not fetch time'
    )
    status: Status
    conflicts: list[Conflict7] | None = None
    method: Method | None = Method.reported
    formula: str | None = Field(
        None, description='required when method=derived, e.g. (bid+ask)/2'
    )
    unit: str | None = None
    note: str | None = None


class Instrument1(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    symbol: str
    name: str | None = None
    region: Region
    level: NumberField
    change_pct: NumberField
    session_state: SessionState


class GlobalSnapshot(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Id
    instruments: list[Instrument1]
    themes: list[StringField] | None = None


class Row(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    rank: int = Field(..., ge=1)
    ticker: Ticker
    last: NumberField | None = None
    change_pct: NumberField | None = None
    volume: NumberField | None = None
    relative_volume: NumberField | None = None


class Screen1(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    screen_id: Id
    name: str = Field(..., description='e.g. ta_toplosers')
    source_id: SourceId
    filters: str | None = None
    fetched_at: Timestamp
    status: Status
    status_reason: str | None = None
    parse_notes: str | None = None
    rows: list[Row]


class EarningsReport(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Id
    ticker: Ticker
    fiscal_period: str
    reported: BoolField
    eps_actual: NumberField
    eps_estimate: NumberField
    revenue_actual_usd_bn: NumberField
    revenue_estimate_usd_bn: NumberField
    premarket_change_pct: NumberField
    extra_metrics: dict[str, NumberField] | None = None


class PremarketScan(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Id
    screens: list[Screen1]
    earnings_reports: list[EarningsReport]


class MarketCalendar(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    timezone: Literal['America/New_York']
    is_trading_day: bool
    early_close: bool | None = False
    regular_open: TsPair = Field(
        ...,
        description='09:30 ET on session.date, with Berlin equivalent computed via zoneinfo',
    )
    regular_close: TsPair = Field(
        ..., description='16:00 ET (13:00 on early-close days)'
    )
    ny_berlin_offset_hours: NyBerlinOffsetHours | None = Field(
        None,
        description='Informational only: 6 normally, 5 during DST-mismatch weeks. Never used for computation.',
    )


class Session(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: str = Field(
        ...,
        description='sess-YYYY-MM-DD[-suffix]; date part = US trading date in America/New_York',
        pattern='^sess-\\d{4}-\\d{2}-\\d{2}(-[a-z0-9]+)?$',
    )
    date: Date = Field(..., description='US trading date (America/New_York)')
    mode: Literal['paper'] = Field(
        ..., description='Paper only. Any other value is invalid by design.'
    )
    created: TsPair
    updated: TsPair
    risk_ruleset_version: str | None = Field(
        None, description='version of risk_rules.yaml used by reviews in this session'
    )
    market_calendar: MarketCalendar
    notes: list[str] | None = None


class Source(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: SourceId
    url: str = Field(
        ...,
        description="https URL; 'urn:poodle:derived' for values the pipeline computed",
    )
    fetched_at: Timestamp
    kind: Kind
    publisher: str | None = None
    title: str | None = None
    realtime: bool | None = Field(
        None, description='Whether the source claims realtime data'
    )
    http_status: int | None = None
    notes: str | None = None


class Run(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Id
    phase: Phase = Field(
        ...,
        description='NY clock. closed = before 04:00 or after the regular close (16:00, 13:00 on early-close days) on a trading day, or a weekend / NYSE holiday',
    )
    started: TsPair
    market_status: StringField = Field(
        ...,
        description='e.g. Pre-Market / Open / Closed as reported by the quote source',
    )
    summary: str | None = None


class Screen(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    screen_id: Id
    name: str = Field(..., description='e.g. ta_toplosers')
    source_id: SourceId
    filters: str | None = None
    fetched_at: Timestamp
    status: Status
    status_reason: str | None = None
    parse_notes: str | None = None
    rows: list[Row]


class PremarketScanModel(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Id
    screens: list[Screen]
    earnings_reports: list[EarningsReport]


class Instrument(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    symbol: str
    name: str | None = None
    region: Region
    level: NumberField
    change_pct: NumberField
    session_state: SessionState


class GlobalSnapshotModel(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Id
    instruments: list[Instrument]
    themes: list[StringField] | None = None


class Event(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    event_id: Id
    kind: EventKind
    underlying: Ticker | None = Field(..., description='null for macro events')
    scheduled_at: TimestampField = Field(
        ...,
        description='Event instant (America/New_York offset). If time unknown, use 00:00 NY and timing=unknown; rules then treat the whole NY calendar day as the event.',
    )
    timing: Timing
    dividend_amount: NumberField | None = Field(
        None, description='ex_dividend only: cash dividend per share'
    )
    description: str | None = None


class CoverageItem(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    kind: EventKind
    underlying: Ticker | None
    checked_through: DateField = Field(
        ...,
        description='Calendar verified complete through this NY date (inclusive). status missing => not checked.',
    )


class EventCalendar(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    events: list[Event]
    coverage: list[CoverageItem]


class Quote(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    last: NumberField
    bid: NumberField
    ask: NumberField
    prev_close: NumberField
    change_pct: NumberField
    day_high: NumberField | None = None
    day_low: NumberField | None = None
    week52_low: NumberField | None = None
    week52_high: NumberField | None = None


class Volume(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    volume: NumberField = Field(
        ..., description='session-to-date (premarket volume if before 09:30 ET)'
    )
    avg_volume: NumberField = Field(..., description='30-day average daily volume')
    volume_vs_avg: NumberField = Field(..., description='derived: volume / avg_volume')


class Iv(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    iv30_current: NumberField = Field(..., description='decimal, 0.25 = 25%')
    iv30_history: NumberArrayField = Field(
        ..., description='trailing 30 calendar days of daily IV30 closes, oldest first'
    )


class Pricing(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Id
    reference_mid: Number | None = Field(
        ..., description='net per-share price at leg mids (debit positive)'
    )
    natural: Number | None = Field(
        ..., description='net per-share price crossing the spread'
    )
    status: Status = Field(..., description='worst status among the leg quotes used')
    note: str | None = None


class HoldWindow(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    start: TsPair = Field(..., description='earliest intended entry')
    end: TsPair = Field(
        ..., description='time stop / latest planned exit (<= expiry 16:00 ET)'
    )


class DmAlternative(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    alt_id: Id
    kind: Kind1
    description: str
    ticket_id: Id | None = None
    decision: Decision
    reason: str


class Ticket(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ticket_id: Id
    candidate_id: Id
    label: Literal['HYPOTHETICAL - paper only, not an order']
    created: TsPair
    underlying: Ticker
    strategy: Strategy
    direction: Direction | None = None
    thesis: str | None = None
    legs: list[Leg] = Field(..., min_length=1)
    quantity: int = Field(
        ...,
        description='number of spread units; leg contracts = quantity * leg.ratio',
        ge=1,
    )
    limit_price: LimitPrice
    pricing: Pricing
    risk_basis: str | None = Field(
        None,
        description='e.g. which per-trade limit was used (placeholder until Eugene sets one)',
    )
    hold_window: HoldWindow
    entry_rules: list[TradeRule]
    exit_rules: list[TradeRule]
    stop_rules: list[TradeRule]
    target_rules: list[TradeRule]
    max_loss: PayoffAmount
    max_profit: PayoffAmount
    breakevens: list[Breakeven]
    intended_event_ids: list[Id] = Field(
        ...,
        description='events the ticket deliberately holds through (e.g. an earnings play); lets the earnings rule FLAG instead of VETO',
    )
    dm_alternatives: list[DmAlternative] = Field(..., min_length=1)


class Input(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    path: str
    value: Any | None = None
    status: Status
    source_id: SourceId | None = None


class PerLegItem(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    leg_id: Id
    result: RuleResult
    measured: Any
    reason: str | None = None


class Result(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    rule_id: str = Field(..., pattern='^R\\d{2}_[A-Z0-9_]+$')
    severity: Severity
    result: RuleResult
    measured: Any = Field(
        ...,
        description='value the formula produced (number, string, object, or null when UNKNOWN)',
    )
    threshold: Any = Field(..., description='threshold compared against (from config)')
    reason: str = Field(..., min_length=1)
    placeholder: bool | None = Field(
        None, description='threshold is a placeholder default pending Eugene'
    )
    inputs: list[Input] | None = None
    per_leg: list[PerLegItem] | None = None


class RiskReview(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    review_id: Id
    ticket_id: Id
    run_id: Id
    evaluated_at: TsPair
    ruleset_version: str
    config_overrides: dict[str, Any] | None = None
    market_open_at_evaluation: bool = Field(
        ..., description='computed in America/New_York'
    )
    results: list[Result] = Field(..., min_length=1)
    overall: Overall


class Approval(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    approval_id: Id
    ticket_id: Id
    review_id: Id
    approver: Approver
    approved_at: TsPair
    expires_at: TsPair = Field(
        ...,
        description='default: same NY trading day regular close; must be > approved_at',
    )
    legs: list[Leg] = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)
    limit_price: LimitPrice
    acknowledged_flags: list[str] | None = Field(
        None, description='FLAG rule_ids the approver explicitly accepted'
    )
    status: Status1
    status_changed_at: TsPair | None = None


class Fill(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    fill_id: Id
    leg_id: Id
    contract_id: OccSymbol
    action: Action
    qty: int = Field(..., ge=1)
    price: Number = Field(..., ge=0.0)
    fees_usd: Number | None = Field(0, ge=0.0)
    filled_at: TsPair
    quote_source_id: SourceId
    simulated: Literal[True]


class LegMark(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    leg_id: Id
    mid: NumberField


class Mark(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    marked_at: TsPair
    underlying_last: NumberField
    leg_marks: list[LegMark] | None = None
    position_value: NumberField = Field(
        ..., description='net per-share value of the structure'
    )
    unrealized_pnl_usd: NumberField


class Close(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    closed_at: TsPair
    reason: Reason
    realized_pnl_usd: Number
    note: str | None = None


class PaperPosition(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    position_id: Id
    ticket_id: Id
    approval_id: Id
    status: Status2
    fill_model: FillModel | None = Field(
        None, description='how simulated fills are priced'
    )
    fills: list[Fill]
    marks: list[Mark]
    close: Close | None = None


class Target(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ticket_id: Id
    position_id: Id | None = None


class WatcherRule(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    watch_id: Id
    target: Target
    kind: Kind2
    conditions: list[Condition] = Field(
        ...,
        description='ANY condition true -> alert (OR); use separate watches for AND',
        min_length=1,
    )
    severity: Severity1
    action: Action1
    active: bool = Field(
        ..., description='false = defined only (e.g. ticket not yet approved)'
    )
    schedule: str | None = Field(
        None, description='cron in America/New_York, or null if no schedule created'
    )
    note: str | None = None


class Alert(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    alert_id: Id
    watch_id: Id
    triggered_at: TsPair
    observed: NumberField
    message: str
    acknowledged: bool
    acknowledged_at: TsPair | None = None


class HistoryItem(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    status: IdeaStatus
    at: TsPair
    actor: Actor
    reason: str = Field(..., min_length=1)
    ref_ids: list[str] | None = None


class IdeaLogItem(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    idea_id: Id
    description: str
    ticket_id: Id | None = None
    current_status: IdeaStatus
    history: list[HistoryItem] = Field(..., min_length=1)


class OptionContract(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    contract_id: OccSymbol
    type: Type
    strike: Number = Field(..., gt=0.0)
    expiry: Date
    style: Style | None = Style.american
    multiplier: int | None = 100
    bid: NumberField
    ask: NumberField
    mid: NumberField = Field(
        ..., description='derived (bid+ask)/2; status = worst of bid/ask'
    )
    last: NumberField | None = None
    iv: NumberField = Field(..., description='decimal')
    open_interest: IntegerField = Field(
        ..., description='OCC open interest (contracts, integer)'
    )
    volume: NumberField
    greeks: Greeks


class OptionChainSlice(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expiry: Date
    source_id: SourceId
    slice_note: str | None = None
    contracts: list[OptionContract]


class Snapshot(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Id
    quote: Quote
    volume: Volume
    iv: Iv | None = None
    option_chain_slices: list[OptionChainSlice]


class Candidate(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    candidate_id: Id
    underlying: Ticker
    company_name: str | None = None
    exchange: str | None = None
    catalysts: list[StringField]
    related_event_ids: list[Id] | None = None
    snapshots: list[Snapshot] = Field(..., min_length=1)


class PaperUSOptionsTradingSessionRecord(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: SchemaVersion
    session: Session
    sources: list[Source]
    runs: list[Run] = Field(
        ...,
        description='Each pipeline pass within the session (premarket scan, preopen update, post-open requote...). Time-varying records point at a run_id.',
        min_length=1,
    )
    premarket_scan: PremarketScanModel
    global_snapshot: GlobalSnapshotModel
    event_calendar: EventCalendar = Field(
        ...,
        description="Scheduled events the risk rules need (earnings, ex-dividend, CPI, PPI, FOMC, NFP). 'coverage' records how far each calendar was actually checked, so 'no event found' (PASS) is distinguishable from 'never checked' (UNKNOWN).",
    )
    candidates: list[Candidate]
    tickets: list[Ticket]
    risk_reviews: list[RiskReview]
    approvals: list[Approval] = Field(
        ...,
        description='Contract-level approvals. Bound to exact legs + quantity + limit; any change requires a new approval. Never valid for a VETO review.',
    )
    paper_positions: list[PaperPosition]
    watcher_rules: list[WatcherRule]
    alerts: list[Alert]
    idea_log: list[IdeaLogItem]
    data_quality: DataQuality = Field(
        ...,
        description='Derived index of non-ok Fields (JSON Pointer paths) for the UI; must be regenerable from the record.',
    )
    premarket_scans: list[PremarketScan] | None = Field(
        None,
        description='One premarket scan per run (run_id unique). premarket_scan, if present, is the latest of these (kept for v1 readers).',
    )
    global_snapshots: list[GlobalSnapshot] | None = Field(
        None,
        description='One global snapshot per run (run_id unique). global_snapshot, if present, is the latest of these (kept for v1 readers).',
    )
    trace_ref: TraceRef | None = Field(
        None, description='Hand-off trace written next to this record (not embedded).'
    )


SessionRecordV11 = PaperUSOptionsTradingSessionRecord
