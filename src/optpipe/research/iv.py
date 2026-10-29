"""Derived implied volatility. Owner: Doodle (method) / Poodle (code).

* Per contract: Black-Scholes (European, NO dividends) solved by bisection so that the model price
  equals the chain mid. Listed equity options are American; for calls without a dividend before
  expiry this matches, and for puts (early exercise) and dividend payers the European IV is an
  approximation. The record's formula/notes say so.
* iv30_current: ATM IV per expiry (call/put average, interpolated linearly in strike around spot),
  then linear interpolation in total variance (sigma^2 * T) to `target_days`; flat extrapolation
  when 30 days is outside the listed expiries (noted).
* iv30_history: one observation per NY date in a JSONL file per underlying, kept under
  <state_dir>/<data_mode>/iv_history/. History = observations from the 30 calendar days before the
  run date that were recorded at or before the run's as_of. R10 stays UNKNOWN below 20 observations.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

YEAR_SECONDS = 365 * 24 * 3600
IV_LO, IV_HI = 1e-4, 5.0
BS_NOTE = "Black-Scholes European, no dividends; American early exercise/dividends ignored"


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _is_call(right: str) -> bool:
    """'C'/'call' (any case) -> True; 'P'/'put' -> False."""
    r = right.strip().upper()
    if r not in ("C", "CALL", "P", "PUT"):
        raise ValueError(f"option right must be C/P/call/put, got {right!r}")
    return r.startswith("C")


def bs_price(right: str, s: float, k: float, t: float, r: float, sigma: float) -> float:
    call = _is_call(right)
    if t <= 0 or sigma <= 0:
        return max(0.0, (s - k) if call else (k - s))
    sq = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + sigma * sigma / 2) * t) / sq
    d2 = d1 - sq
    if call:
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def implied_vol(right: str, price: float, s: float, k: float, t: float, r: float,
                tol: float = 1e-6, max_iter: int = 200) -> Optional[float]:
    """Bisection on sigma in [1e-4, 5]. None when price is outside the no-arbitrage band."""
    if not (price > 0 and s > 0 and k > 0 and t > 0):
        return None
    lo_p, hi_p = bs_price(right, s, k, t, r, IV_LO), bs_price(right, s, k, t, r, IV_HI)
    if not (lo_p - tol <= price <= hi_p + tol):
        return None
    lo, hi = IV_LO, IV_HI
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if bs_price(right, s, k, t, r, mid) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def year_fraction(start: datetime, expiry_close: datetime) -> float:
    return max(0.0, (expiry_close - start).total_seconds() / YEAR_SECONDS)


@dataclass
class ExpiryIv:
    expiry: date
    t: float                  # years
    atm_iv: float


def atm_iv(spot: float, points: list[tuple[float, str, float]]) -> Optional[float]:
    """points: (strike, right, iv). Average call/put IV per strike, then linear in strike at spot."""
    by_k: dict[float, list[float]] = {}
    for k, _, iv in points:
        by_k.setdefault(k, []).append(iv)
    ks = sorted(by_k)
    if not ks:
        return None
    avg = {k: sum(v) / len(v) for k, v in by_k.items()}
    below = [k for k in ks if k <= spot]
    above = [k for k in ks if k >= spot]
    if below and above:
        k0, k1 = below[-1], above[0]
        if k0 == k1:
            return avg[k0]
        w = (spot - k0) / (k1 - k0)
        return avg[k0] * (1 - w) + avg[k1] * w
    return avg[min(ks, key=lambda k: abs(k - spot))]


def interpolate_iv30(per_expiry: list[ExpiryIv], target_days: int = 30) -> tuple[Optional[float], str]:
    """Linear in total variance between the expiries bracketing target_days. -> (iv, how)."""
    xs = sorted((e for e in per_expiry if e.t > 0), key=lambda e: e.t)
    if not xs:
        return None, "no expiry with a usable ATM IV"
    tt = target_days / 365
    lo = [e for e in xs if e.t <= tt]
    hi = [e for e in xs if e.t >= tt]
    if lo and hi:
        a, b = lo[-1], hi[0]
        if a.expiry == b.expiry:
            return a.atm_iv, f"ATM IV of {a.expiry.isoformat()}"
        wa, wb = a.atm_iv ** 2 * a.t, b.atm_iv ** 2 * b.t
        w = wa + (wb - wa) * (tt - a.t) / (b.t - a.t)
        return math.sqrt(max(w, 0) / tt), (f"total-variance interpolation between {a.expiry.isoformat()} "
                                            f"and {b.expiry.isoformat()} to {target_days}d")
    e = lo[-1] if lo else hi[0]
    return e.atm_iv, f"flat extrapolation from {e.expiry.isoformat()} (no expiry on the other side of {target_days}d)"


class IvHistoryStore:
    """Append-only JSONL per underlying: {"date", "iv30", "recorded_at", "run_id", "data_mode"}."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _path(self, symbol: str) -> Path:
        return self.dir / f"{symbol.upper()}.jsonl"

    def load(self, symbol: str) -> list[dict]:
        p = self._path(symbol)
        if not p.exists():
            return []
        out = []
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def history(self, symbol: str, run_date: date, as_of: datetime, days: int = 30) -> list[tuple[date, float]]:
        """Last observation per date in [run_date - days, run_date), recorded at or before as_of."""
        best: dict[date, tuple[datetime, float]] = {}
        for o in self.load(symbol):
            d, rec = date.fromisoformat(o["date"]), datetime.fromisoformat(o["recorded_at"])
            if run_date - timedelta(days=days) <= d < run_date and rec <= as_of:
                if d not in best or rec >= best[d][0]:
                    best[d] = (rec, float(o["iv30"]))
        return [(d, best[d][1]) for d in sorted(best)]

    def record(self, symbol: str, obs_date: date, iv30: float, recorded_at: datetime, run_id: str,
               data_mode: str) -> None:
        """One row per (date, run); re-running the same run_id/date does not duplicate."""
        rows = self.load(symbol)
        if any(o["date"] == obs_date.isoformat() and o.get("run_id") == run_id for o in rows):
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._path(symbol).open("a") as f:
            f.write(json.dumps({"date": obs_date.isoformat(), "iv30": round(iv30, 6),
                                "recorded_at": recorded_at.isoformat(), "run_id": run_id,
                                "data_mode": data_mode}) + "\n")
