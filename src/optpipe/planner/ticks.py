"""Option price tick-size rounding (Doodle's ruling). Owner: Doodle (rule) / Poodle (code).

Penny-pilot names (configurable list): 0.01 below $3.00, 0.05 at/above $3.00.
Everything else:                       0.05 below $3.00, 0.10 at/above $3.00.
Round in the SAFE direction: debit limit DOWN (never pay more than the cap), stop UP (exit no
later than intended), target DOWN (take profit no later than intended). Raw and rounded are both kept.
Exact decimal arithmetic avoids 3.1006 -> 3.0999999 artefacts.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Iterable, Literal

Direction = Literal["down", "up", "nearest"]
DEFAULT_PENNY_PILOT = ("SPY", "QQQ", "IWM", "JPM")


@dataclass(frozen=True)
class TickRule:
    penny_pilot: frozenset[str]
    threshold: float = 3.0
    penny_ticks: tuple[float, float] = (0.01, 0.05)
    standard_ticks: tuple[float, float] = (0.05, 0.10)

    @classmethod
    def from_list(cls, names: Iterable[str], **kw) -> "TickRule":
        return cls(frozenset(n.upper() for n in names), **kw)

    def tick(self, underlying: str, price: float) -> float:
        lo, hi = self.penny_ticks if underlying.upper() in self.penny_pilot else self.standard_ticks
        return lo if abs(price) < self.threshold else hi

    def round(self, underlying: str, price: float, direction: Direction) -> "Rounded":
        t = self.tick(underlying, price)
        q = Decimal(str(t))
        mode = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_UP}[direction]
        # repr-based Decimal keeps 3.1006 exact; quantize to the tick grid
        steps = (Decimal(repr(price)) / q).to_integral_value(rounding=mode)
        val = float(steps * q)
        # rounding down across the $3 boundary can land on a finer grid; re-check with the new level
        if direction == "down" and abs(val) < self.threshold <= abs(price):
            t2 = self.tick(underlying, val)
            val = float((Decimal(repr(price)) / Decimal(str(t2))).to_integral_value(rounding=mode) * Decimal(str(t2)))
            t = t2
        return Rounded(raw=price, value=round(val, 2), tick=t, direction=direction)


@dataclass(frozen=True)
class Rounded:
    raw: float
    value: float
    tick: float
    direction: str

    def text(self) -> str:
        return f"{self.raw:.4f} -> {self.value:.2f} ({self.direction} to {self.tick:.2f} tick)"
