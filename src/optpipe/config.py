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


class Settings(_M):
    mode: Literal["paper_only"]          # structurally the only accepted value
    user: UserCfg = Field(default_factory=UserCfg)
    broker: BrokerCfg = Field(default_factory=BrokerCfg)
    trade_style: str = "UNKNOWN"
    watchlist: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    spec_dir: str = "/workspace/trading/spec"
    market: MarketCfg = Field(default_factory=MarketCfg)
    discovery: DiscoveryCfg
    http: HttpCfg
    providers: ProvidersCfg = Field(default_factory=ProvidersCfg)
    research: ResearchCfg = Field(default_factory=ResearchCfg)
    planner: PlannerCfg
    risk_overrides: dict = Field(default_factory=dict)   # applied on top of spec risk_rules.yaml config

    @property
    def spec_path(self) -> Path:
        return Path(os.environ.get("OPTIONS_SPEC_DIR", self.spec_dir))


def load_settings(path: str | Path | None = None) -> Settings:
    p = Path(path) if path else CONFIG_DIR / "settings.yaml"
    return Settings.model_validate(yaml.safe_load(p.read_text()))
