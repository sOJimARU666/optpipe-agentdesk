"""Generate tests/fixtures/2026-10-13/watch_tape.yaml: a HAND-MADE, FICTIONAL intraday quote tape for
`optpipe watch --replay`. It is NOT market data.

Underlyings start at the 2026-10-13 07:52 NY Nasdaq capture (JPM 336.9145, WDC 363.40) and follow a
seeded random walk. Options are Black-Scholes mids at each contract's captured IV, with a spread of
max(0.05, 4% of mid). One tick every 5 minutes from 09:30 to 16:00 NY, plus a simulated 25-minute feed gap
(no ticks 12:10-12:25 NY), so the stale-data path (W06) can be replayed. Re-run to regenerate:
    .venv/bin/python scripts/make_watch_tape.py
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

NY = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "2026-10-13" / "watch_tape.yaml"
SEED = 20261013
START = {"JPM": 336.9145, "WDC": 363.40}
DAILY_VOL = {"JPM": 0.012, "WDC": 0.035}
DRIFT = {"JPM": -0.0004, "WDC": 0.0006}          # per 5-min step, fictional
IV = {"JPM261030C00340000": 0.257082, "JPM261030C00350000": 0.25356, "JPM261030P00325000": 0.256219,
      "JPM261030P00335000": 0.24622, "WDC261030C00365000": 0.564541, "WDC261030C00375000": 0.635281,
      "WDC261030P00350000": 0.634952, "WDC261030P00360000": 0.634392}
EXPIRY = datetime(2026, 10, 30, 16, 0, tzinfo=NY)
R = 0.04
GAP = (datetime(2026, 10, 13, 12, 5, tzinfo=NY), datetime(2026, 10, 13, 12, 30, tzinfo=NY))


def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, T, s, call):
    d1 = (math.log(S / K) + (R + s * s / 2) * T) / (s * math.sqrt(T))
    d2 = d1 - s * math.sqrt(T)
    if call:
        return S * ncdf(d1) - K * math.exp(-R * T) * ncdf(d2)
    return K * math.exp(-R * T) * ncdf(-d2) - S * ncdf(-d1)


def r2(x):
    return float(f"{max(x, 0.0):.2f}")


def main() -> None:
    rnd = random.Random(SEED)
    px = dict(START)
    t = datetime(2026, 10, 13, 9, 30, tzinfo=NY)
    ticks = []
    while t <= datetime(2026, 10, 13, 16, 0, tzinfo=NY):
        if ticks:
            for s in px:
                px[s] *= math.exp(DRIFT[s] + DAILY_VOL[s] / math.sqrt(78) * rnd.gauss(0, 1))
        if not (GAP[0] < t < GAP[1]):
            T = (EXPIRY - t).total_seconds() / (365 * 86400)
            opts = {}
            for cid, iv in IV.items():
                sym, call, k = cid[:3], cid[9] == "C", int(cid[10:]) / 1000
                m = bs(px[sym], k, T, iv, call)
                half = max(0.05, 0.04 * m) / 2
                opts[cid] = [r2(m - half), r2(m + half), round(iv, 6)]
            ticks.append({"at": t.isoformat(), "underlyings": {s: round(v, 2) for s, v in px.items()}, "options": opts})
        t += timedelta(minutes=5)
    doc = {"label": "HAND-MADE FICTIONAL replay tape - not market data (see scripts/make_watch_tape.py)",
           "date": "2026-10-13", "generator": {"script": "scripts/make_watch_tape.py", "seed": SEED,
           "model": "seeded random walk + Black-Scholes at captured IV, spread max(0.05, 4% of mid)",
           "gap": [GAP[0].isoformat(), GAP[1].isoformat()]},
           "ticks": ticks}
    OUT.write_text("# " + doc["label"] + "\n" + yaml.safe_dump(doc, sort_keys=False, width=200, default_flow_style=None))
    print(f"wrote {OUT} ({len(ticks)} ticks)")


if __name__ == "__main__":
    main()
