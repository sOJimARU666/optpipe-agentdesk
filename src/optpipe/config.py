"""Settings loading. Owner: Moodle (what is configurable) / Poodle (code)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserCfg(_M):
    name: str = "Eugene Ward"
    display_timezone: str = "Europe/Berlin"


class BrokerCfg(_M):
    name: str = "UNKNOWN"   # informational only


class ScenarioCfg(_M):
    """A named demo/persona scenario. `fictional: true` puts a FICTIONAL DEMO tag on every output.
    Nothing here is ever created or connected: repo_name is only a proposal (no repo exists)."""
    name: str
    fictional: bool = True
    repo_name: str | None = None            # PROPOSAL only, no GitHub/remote repo is created
    account_size_usd: float | None = None   # fictional paper account
    max_risk_per_idea_pct: float | None = None
    risk_profile: str | None = None         # profile in Doodle's risk_rules.yaml `profiles`

    @property
    def max_loss_per_idea_usd(self) -> float | None:
        """Derived: account size x risk % (25,000 x 2% = 500)."""
        if self.account_size_usd is None or self.max_risk_per_idea_pct is None:
            return None
        return round(self.account_size_usd * self.max_risk_per_idea_pct / 100, 2)


class MarketCfg(_M):
    timezone: str = "America/New_York"
    open_time: str = "09:30"
    entry_not_before: str = "09:45"


class DiscoveryCfg(_M):
    screens: list[str]
    finviz_filters: str
    max_candidates: int = 4
    global_indexes: list[str] = Field(default_factory=list)


class HttpCfg(_M):
    user_agent: str
    timeout_s: float = 20


class ProvidersCfg(_M):
    quotes: str = "nasdaq"
    options: str = "nasdaq"


class ResearchCfg(_M):
    strike_window_pct: float = 10


class SourcesCfg(_M):
    """Macro calendars + risk-free rate (read-only GETs, cached). Owner: Doodle (which) / Poodle (code)."""
    cache_dir: str = "var/cache"                 # relative to the repo root unless absolute
    cache_ttl_hours: float = 12
    fomc_url: str = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    # BLS: bls.gov only serves scripted GETs whose User-Agent carries a contact email. Empty -> BLS is skipped
    # (FRED fallback). Never hard-code a real address here; set it in a local settings file.
    bls_contact_email: str = ""
    bls_ics_url: str = "https://www.bls.gov/schedule/news_release/bls.ics"
    fred_calendar_url: str = "https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={year}"
    fred_release_ids: dict[str, int] = Field(default_factory=lambda: {"CPI": 10, "PPI": 46, "NFP": 50})
    treasury_url: str = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                         "daily-treasury-rates.csv/{year}/all?type=daily_treasury_bill_rates"
                         "&field_tdr_date_value={year}&page&_format=csv")
    treasury_tenor: str = "13 WEEKS COUPON EQUIVALENT"
    replay_file: str = "macro_calendars.yaml"     # fixture-mode calendar/rate snapshot inside the fixture dir


class IvCfg(_M):
    min_history_points: int = 20                  # R10 stays a soft UNKNOWN below this
    target_days: int = 30
    state_dir: str = "var/state"                  # per data_mode: <state_dir>/<data_mode>/iv_history/<SYM>.jsonl


class PlannerCfg(_M):
    max_risk_per_trade_usd: float | None = None   # None -> Risk Officer's per_trade_max_loss_usd
    preferred_dte_min: int = 14
    preferred_dte_max: int = 45
    strike_step: float = 5
    spread_width: float = 10
    stop_fraction_of_debit: float = 0.5
    target_multiple_of_debit: float = 2.0
    entry_slippage_pct: float = 2.5
    time_stop_days_before_expiry: int = 3
    penny_pilot: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM", "JPM"])
    tick_threshold: float = 3.0


class Settings(_M):
    mode: Literal["paper_only"]          # structurally the only accepted value
    user: UserCfg = Field(default_factory=UserCfg)
    broker: BrokerCfg = Field(default_factory=BrokerCfg)
    trade_style: str = "UNKNOWN"
    watchlist: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    scenario: ScenarioCfg | None = None
    spec_dir: str = "/workspace/trading/spec"
    market: MarketCfg = Field(default_factory=MarketCfg)
    discovery: DiscoveryCfg
    http: HttpCfg
    providers: ProvidersCfg = Field(default_factory=ProvidersCfg)
    research: ResearchCfg = Field(default_factory=ResearchCfg)
    planner: PlannerCfg
    sources: SourcesCfg = Field(default_factory=SourcesCfg)
    iv: IvCfg = Field(default_factory=IvCfg)
    risk_overrides: dict = Field(default_factory=dict)   # applied on top of spec risk_rules.yaml config

    @property
    def spec_path(self) -> Path:
        return Path(os.environ.get("OPTIONS_SPEC_DIR", self.spec_dir))


def repo_path(p: str | Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else REPO_ROOT / q


def load_settings(path: str | Path | None = None) -> Settings:
    p = Path(path) if path else CONFIG_DIR / "settings.yaml"
    raw = yaml.safe_load(p.read_text())
    email = os.environ.get("OPTPIPE_BLS_CONTACT_EMAIL")      # local override; never committed
    if email is not None:
        raw.setdefault("sources", {})["bls_contact_email"] = email.strip()
    return Settings.model_validate(raw)
