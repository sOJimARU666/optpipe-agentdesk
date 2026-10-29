"""Load Doodle's risk_rules.yaml (read-only, in place) into a typed Ruleset. No local fallback."""
from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..spec import SpecFiles


class MarketHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: Literal["America/New_York"]
    open: time
    close: time
    early_close: time
    holidays: list[date] = Field(default_factory=list)
    early_closes: list[date] = Field(default_factory=list)


class RiskConfig(BaseModel):
    """Doodle's `config` block. Decimal-valued thresholds are strings in YAML -> exact Decimal."""
    model_config = ConfigDict(extra="allow")
    per_trade_max_loss_usd: Optional[Decimal] = None
    max_underlying_risk_usd: Decimal
    max_total_open_risk_usd: Decimal
    max_open_positions: int
    # ruleset 1.1.0 (R11 sector cap, R12 structures, R13 hold period); absent in 1.0.0 -> rule not checked
    max_sector_risk_usd: Optional[Decimal] = None
    sector_map: dict[str, str] = Field(default_factory=dict)
    allowed_structures: list[str] = Field(default_factory=list)
    hold_min_days: Optional[int] = None
    hold_max_days: Optional[int] = None
    placeholders: list[str] = Field(default_factory=list)
    quote_max_age_seconds: int
    quote_future_tolerance_seconds: int
    max_spread_pct_of_mid: Decimal
    min_open_interest: int
    min_dte: int
    net_price_basis: Literal["mid", "natural"]
    fees_per_contract_usd: Decimal
    macro_event_kinds: list[str]
    min_put_extrinsic: Decimal
    iv_min_history_obs: int
    iv_rank_high: Decimal
    iv_rank_low: Decimal
    contract_multiplier: int
    market_hours: MarketHours


class RuleDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    severity: Literal["hard", "soft"]
    placeholder: bool = False
    description: str = ""


class Ruleset(BaseModel):
    model_config = ConfigDict(extra="allow")
    ruleset_version: str
    config: RiskConfig
    rules: list[RuleDef]
    source: str = ""
    raw_config: dict[str, Any] = Field(default_factory=dict)
    profiles: dict[str, Any] = Field(default_factory=dict)
    profile: Optional[str] = None

    def profile_overrides(self, name: Optional[str]) -> dict[str, Any]:
        """config_overrides of a named profile in risk_rules.yaml `profiles` (None -> {})."""
        if name is None:
            return {}
        if name not in self.profiles:
            raise KeyError(f"risk profile {name!r} not in risk_rules.yaml profiles {sorted(self.profiles)}")
        return dict(self.profiles[name].get("config_overrides") or {})

    def with_profile(self, name: Optional[str]) -> "Ruleset":
        """cfg = defaults <- profile overrides (Doodle's evaluate(..., profile=))."""
        if name is None:
            return self
        return self.with_overrides(self.profile_overrides(name)).model_copy(update={"profile": name})

    def with_overrides(self, overrides: dict[str, Any] | None) -> "Ruleset":
        if not overrides:
            return self
        merged = {**self.raw_config, **overrides}
        return self.model_copy(update={"config": RiskConfig.model_validate(merged), "raw_config": merged})


def ruleset_from_dict(d: dict[str, Any], source: str = "") -> Ruleset:
    return Ruleset(ruleset_version=str(d["ruleset_version"]), config=RiskConfig.model_validate(d["config"]),
                   rules=[RuleDef.model_validate(r) for r in d["rules"]], source=source, raw_config=dict(d["config"]),
                   profiles=dict(d.get("profiles") or {}))


class SpecMissing(RuntimeError):
    pass


def load_ruleset(spec: SpecFiles | None) -> tuple[Ruleset, list[str]]:
    """Doodle's spec/risk_rules.yaml, loaded in place. There is deliberately NO local fallback copy:
    the spec is the single source of truth, so without it the Risk Officer refuses to run."""
    if spec is None or (raw := spec.risk_rules()) is None:
        raise SpecMissing("Doodle's risk_rules.yaml not found (set spec_dir or OPTIONS_SPEC_DIR); "
                          "the Risk Officer will not run without it")
    rs = ruleset_from_dict(raw, str(spec.path("risk_rules")))
    notes = [f"risk rules {rs.ruleset_version} from spec: {spec.path('risk_rules')}"]
    if rs.config.placeholders:
        notes.append("PLACEHOLDER thresholds pending Eugene: " + ", ".join(
            f"{k}={rs.raw_config.get(k)}" for k in rs.config.placeholders))
    return rs, notes
