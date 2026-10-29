"""Option price tick-size rounding. Owner: Doodle (rule, spec risk_rules.yaml config.tick_rules) / Poodle (code).

The tiers, the penny-pilot list and the rounding directions are read from Doodle's spec
(risk_rules.yaml `config.tick_rules`, spec 1.1.1, nested `directions.debit` / `directions.credit`); config/settings.yaml `planner.penny_pilot` /
`tick_threshold` are only a fallback when the spec has no tick_rules.

  penny-pilot names: 0.01 below the threshold ($3.00), 0.05 at/above it
  everything else:   0.05 below, 0.10 at/above      (tier chosen by the RAW price, as validate_session does)

Round in the SAFE direction (never worse than intended):
  debit spreads  (spec 1.1.1):            limit DOWN (pay no more), stop UP (exit no later), target DOWN
  credit spreads (spec 1.1.1, config.tick_rules.directions.credit):
                                          min-credit limit UP (collect no less), stop = debit to close DOWN
                                          (exit no later), target = buy-back UP (take profit no later)
Raw and rounded values are both kept. Exact Decimal arithmetic avoids 3.1006 -> 3.0999999 artefacts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Literal, Mapping, Optional

Direction = Literal["down", "up", "nearest"]
Side = Literal["debit", "credit"]
Role = Literal["limit", "stop", "target"]

DEFAULT_PENNY_PILOT = ("SPY", "QQQ", "IWM", "JPM")     # PLACEHOLDER (same as the spec's); verify vs Cboe/OCC
DEBIT_DIRECTIONS: dict[str, str] = {"limit": "down", "stop": "up", "target": "down"}
CREDIT_DIRECTIONS: dict[str, str] = {"limit": "up", "stop": "down", "target": "up"}   # ruling for spec 1.1.1
# spec key -> (side, role); credit keys are accepted once the spec carries them (1.1.1)
_SPEC_DIRECTION_KEYS = {"debit_limit": ("debit", "limit"), "stop": ("debit", "stop"), "target": ("debit", "target"),
                        "debit_stop": ("debit", "stop"), "debit_target": ("debit", "target"),
                        "credit_limit": ("credit", "limit"), "credit_stop": ("credit", "stop"),
                        "credit_target": ("credit", "target")}


def _dirs() -> dict[str, dict[str, str]]:
    return {"debit": dict(DEBIT_DIRECTIONS), "credit": dict(CREDIT_DIRECTIONS)}


@dataclass(frozen=True)
class TickRule:
    penny_pilot: frozenset[str]
    threshold: float = 3.0
    penny_ticks: tuple[float, float] = (0.01, 0.05)
    standard_ticks: tuple[float, float] = (0.05, 0.10)
    directions: Mapping[str, Mapping[str, str]] = field(default_factory=_dirs)
    source: str = "settings.yaml planner.penny_pilot (fallback)"

    @classmethod
    def from_list(cls, names: Iterable[str], **kw) -> "TickRule":
        return cls(frozenset(n.upper() for n in names), **kw)

    @classmethod
    def from_spec(cls, tick_rules: Optional[Mapping[str, Any]], fallback_penny: Iterable[str] = DEFAULT_PENNY_PILOT,
                  fallback_threshold: float = 3.0) -> "TickRule":
        """Doodle's risk_rules.yaml config.tick_rules if present, else the settings fallback."""
        if not tick_rules:
            return cls.from_list(fallback_penny, threshold=fallback_threshold)
        tr = tick_rules
        dirs = _dirs()
        for k, v in (tr.get("directions") or {}).items():
            if k in ("debit", "credit") and isinstance(v, Mapping):        # spec 1.1.1: {debit: {limit,stop,target}, credit: {...}}
                for role, d in v.items():
                    if role in ("limit", "stop", "target"):
                        dirs[k][role] = str(d)
                continue
            if k in _SPEC_DIRECTION_KEYS:
                side, role = _SPEC_DIRECTION_KEYS[k]
                dirs[side][role] = str(v)
        pair = lambda xs, d: tuple(float(Decimal(str(x))) for x in xs) if xs else d  # noqa: E731
        return cls(penny_pilot=frozenset(str(n).upper() for n in (tr.get("penny_pilot") or fallback_penny)),
                   threshold=float(Decimal(str(tr.get("threshold", fallback_threshold)))),
                   penny_ticks=pair(tr.get("penny_ticks"), (0.01, 0.05)),
                   standard_ticks=pair(tr.get("standard_ticks"), (0.05, 0.10)),
                   directions=dirs, source="spec risk_rules.yaml config.tick_rules")

    def direction(self, side: Side, role: Role) -> str:
        return self.directions[side][role]

    def tick(self, underlying: str, price: float) -> float:
        lo, hi = self.penny_ticks if underlying.upper() in self.penny_pilot else self.standard_ticks
        return lo if abs(price) < self.threshold else hi

    def round(self, underlying: str, price: float, direction: Direction) -> "Rounded":
        # Tier by the RAW price (as Doodle's validator does). Rounding down from >= threshold cannot land on a
        # finer grid: the threshold is itself on the coarser grid, so no boundary re-check is needed.
        t = self.tick(underlying, price)
        q = Decimal(str(t))
        mode = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_UP}[direction]
        steps = (Decimal(repr(price)) / q).to_integral_value(rounding=mode)    # repr keeps 3.1006 exact
        return Rounded(raw=price, value=round(float(steps * q), 2), tick=t, direction=direction)

    def round_for(self, underlying: str, price: float, side: Side, role: Role) -> "Rounded":
        return self.round(underlying, price, self.direction(side, role))     # type: ignore[arg-type]


@dataclass(frozen=True)
class Rounded:
    raw: float
    value: float
    tick: float
    direction: str

    def text(self) -> str:
        return f"{self.raw:.4f} -> {self.value:.2f} ({self.direction} to {self.tick:.2f} tick)"
