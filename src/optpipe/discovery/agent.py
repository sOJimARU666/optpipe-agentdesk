"""Discovery: pre-market screens + global snapshot -> premarket_scan, global_snapshot, picks.

Owner: Doodle (what to scan / how to pick) / Poodle (code).
"""
from __future__ import annotations

from typing import Any

from ..agent import Agent
from ..models.common import Bias, ScreenResult
from ..models.contracts import Catalyst, DiscoveryInput, DiscoveryOutput, Pick

SETTINGS_SRC = "src-settings"
from ..providers.base import GlobalSnapshotProvider, ScreenerProvider
from ..providers.finviz import SCREENER_URL
from ..records.fields import F, src_id

SCREEN_BIAS = {
    "ta_toplosers": Bias.BEARISH, "ta_newlow": Bias.BEARISH,
    "ta_topgainers": Bias.BULLISH, "ta_newhigh": Bias.BULLISH,
    "ta_unusualvolume": Bias.NEUTRAL,
}
# Movers first, then unusual volume, then breakouts.
SCREEN_PRIORITY = ["ta_toplosers", "ta_topgainers", "ta_unusualvolume", "ta_newhigh", "ta_newlow"]
PARSE_NOTES = ("Ticker read from the link/data attribute (raw text doubles the logo letter, e.g. WWDC=WDC). "
               "Page is not timestamped; values may be prior-session -> rows marked stale with as_of = fetch time.")


def screen_section(r: ScreenResult, filters: str, as_of) -> tuple[dict, dict]:
    name = r.screen
    sid = src_id("finviz", name.removeprefix("ta_"))
    fetched = r.fetched_at or as_of
    url = r.source or SCREENER_URL.format(screen=name, filters=filters)
    source = {"id": sid, "url": url, "fetched_at": fetched.isoformat(), "kind": "screener", "publisher": "Finviz",
              "realtime": False}
    rows = []
    for i, row in enumerate(r.rows, 1):
        d: dict[str, Any] = {"rank": i, "ticker": row.ticker}
        if row.price is not None:
            d["last"] = F(row.price, sid, fetched, "stale")
        if row.change_pct is not None:
            d["change_pct"] = F(row.change_pct, sid, fetched, "stale")
        if row.volume is not None:
            d["volume"] = F(row.volume, sid, fetched, "stale")
        rows.append(d)
    sec: dict[str, Any] = {"screen_id": "scr-" + name.removeprefix("ta_"), "name": name, "source_id": sid,
                           "filters": filters, "fetched_at": fetched.isoformat(),
                           "status": "stale" if rows else "missing", "rows": rows, "parse_notes": PARSE_NOTES}
    if not rows:
        sec["status_reason"] = r.note or f"screen status {r.status}: no result rows"
    return sec, source


class DiscoveryAgent(Agent[DiscoveryInput, DiscoveryOutput]):
    stage = "discovery"
    owner = "Doodle (logic) / Poodle (code)"
    input_model = DiscoveryInput
    output_model = DiscoveryOutput

    def __init__(self, screener: ScreenerProvider, global_provider: GlobalSnapshotProvider, filters: str,
                 earnings_reports: list[dict] | None = None, extra_sources: list[dict] | None = None):
        self.screener, self.global_provider, self.filters = screener, global_provider, filters
        self.earnings_reports = earnings_reports or []      # curated (no automated source yet)
        self.extra_sources = extra_sources or []

    def run(self, inp: DiscoveryInput) -> DiscoveryOutput:
        screens, sources, results, notes = [], [], {}, []
        for s in inp.screens:
            r = self.screener.get_screen(s)
            results[s] = r
            sec, src = screen_section(r, self.filters, inp.as_of)
            screens.append(sec); sources.append(src)
            if not r.rows:
                notes.append(f"finviz {s}: {sec['status_reason']}")
        g, gsrc = self.global_provider.get_snapshot(inp.run_id)
        sources += gsrc + self.extra_sources
        if not g["instruments"]:
            notes.append("global snapshot: no instruments")
        reports = [dict(r, run_id=inp.run_id) for r in self.earnings_reports]
        return DiscoveryOutput(
            premarket_scan={"run_id": inp.run_id, "screens": screens, "earnings_reports": reports},  # type: ignore[arg-type]
            global_snapshot=g,  # type: ignore[arg-type]
            sources=_dedupe(sources), picks=self.select(inp, results), notes=notes)  # type: ignore[arg-type]

    def select(self, inp: DiscoveryInput, screens: dict[str, ScreenResult]) -> list[Pick]:
        chosen: dict[str, Pick] = {}
        for m in inp.manual_candidates:
            chosen[m.symbol] = Pick(symbol=m.symbol, bias=m.bias, company_name=m.company_name,
                                    exchange=m.exchange, catalysts=list(m.catalysts))
        for sym in inp.watchlist:
            chosen.setdefault(sym, Pick(symbol=sym, catalysts=[Catalyst(text="watchlist", source_id=SETTINGS_SRC)]))
        for s, r in screens.items():
            for row in r.rows:
                if row.ticker in chosen:
                    chosen[row.ticker].screens.append(s)
                    chosen[row.ticker].catalysts.append(Catalyst(text=f"Finviz {s}", status="stale",
                                                                 source_id=src_id("finviz", s.removeprefix("ta_"))))
        ranked = []
        for s in SCREEN_PRIORITY:
            if r := screens.get(s):
                ranked += [(s, row) for row in sorted(r.rows, key=lambda x: -abs(x.change_pct or 0))]
        for s, row in ranked:
            if len(chosen) >= inp.max_candidates:
                break
            if row.ticker in chosen:
                continue
            txt = f"Finviz {s}: {row.price} {row.change_pct:+.2f}%" if row.change_pct is not None else f"Finviz {s}"
            chosen[row.ticker] = Pick(symbol=row.ticker, bias=SCREEN_BIAS.get(s, Bias.NEUTRAL), screens=[s],
                                      catalysts=[Catalyst(text=txt, status="stale",
                                                          source_id=src_id("finviz", s.removeprefix("ta_")))])
        return list(chosen.values())[: max(inp.max_candidates, len(inp.manual_candidates))]

    def summarize_input(self, inp: DiscoveryInput) -> dict[str, Any]:
        return {"as_of": inp.as_of.isoformat(), "screens": inp.screens, "watchlist": inp.watchlist,
                "manual": [m.symbol for m in inp.manual_candidates], "max_candidates": inp.max_candidates}

    def summarize_output(self, out: DiscoveryOutput) -> dict[str, Any]:
        return {"screens": {s.name: f"{s.status.value}:{len(s.rows)}" for s in out.premarket_scan.screens},
                "global_instruments": len(out.global_snapshot.instruments),
                "picks": [f"{p.symbol}({p.bias.value})" for p in out.picks], "notes": len(out.notes)}


def _dedupe(sources: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for s in sources:
        seen.setdefault(s["id"], s)
    return list(seen.values())
