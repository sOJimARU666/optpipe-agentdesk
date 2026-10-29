"""Research: picks -> schema-v1 candidates (sourced quotes + chain slices) + event calendar.

Owner: Doodle (what to research) / Poodle (code). Never invents a value: anything a provider does
not give is a `missing` Field with a note.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..agent import Agent
from ..models.common import Event as PEvent, OptionChain, QuoteBasis, UnderlyingQuote
from ..models.contracts import ManualCoverage, ManualEvent, Pick, ResearchInput, ResearchOutput
from ..providers.base import DataUnavailable, EventProvider, OptionChainProvider, QuoteProvider
from ..records.fields import F, MISS, NY, derived, ny_at, occ_symbol, slug

MAX_SLICE_DTE = 60          # keep chain slices within ~2 months (record size)
PRIOR_NOTE = "prior-session close quote; options do not trade premarket"
GREEK_NOTE = "not provided by Nasdaq chain"


def _cap(t: Optional[datetime], fetched: datetime) -> datetime:
    """as_of may never be later than fetched_at (INV-09)."""
    return fetched if t is None or t > fetched else t


class ResearchAgent(Agent[ResearchInput, ResearchOutput]):
    stage = "research"
    owner = "Doodle (logic) / Poodle (code)"
    input_model = ResearchInput
    output_model = ResearchOutput

    def __init__(self, quotes: QuoteProvider, chains: OptionChainProvider, events: Optional[EventProvider] = None):
        self.quotes, self.chains, self.events = quotes, chains, events

    def run(self, inp: ResearchInput) -> ResearchOutput:
        cands, sources, notes, bias = [], {}, [], {}
        provider_events: list[PEvent] = []
        for p in inp.picks:
            cid = f"cand-{slug(p.symbol)}"
            bias[cid] = p.bias
            snap, srcs, pev, n = self._snapshot(p, inp)
            sources.update({s["id"]: s for s in srcs})
            provider_events += pev
            notes += n
            cands.append({"candidate_id": cid, "underlying": p.symbol, "company_name": p.company_name,
                          "exchange": p.exchange,
                          "catalysts": [F(c.text, c.source_id, inp.source_times.get(c.source_id, inp.as_of), c.status)
                                        for c in p.catalysts],
                          "snapshots": [snap]})
        events = self._events(inp, provider_events, sources)
        for c in cands:
            c["related_event_ids"] = [e["event_id"] for e in events if e.get("underlying") == c["underlying"]]
            if c["company_name"] is None:
                del c["company_name"]
            if c["exchange"] is None:
                del c["exchange"]
        coverage = [self._coverage(c, inp.source_times.get(c.source_id or "", inp.as_of)) for c in inp.coverage]
        have = {(c["kind"], c["underlying"]) for c in coverage}
        for p in inp.picks:                       # be explicit about what was NOT checked
            for kind in ("earnings", "ex_dividend"):
                if (kind, p.symbol) not in have:
                    coverage.append({"kind": kind, "underlying": p.symbol,
                                     "checked_through": MISS(note="no calendar source configured for this symbol")})
        return ResearchOutput(candidates=cands, event_calendar={"events": events, "coverage": coverage},  # type: ignore[arg-type]
                              sources=list(sources.values()), bias=bias, notes=notes)  # type: ignore[arg-type]

    # ---- quotes + chain ----
    def _snapshot(self, p: Pick, inp: ResearchInput):
        notes: list[str] = []
        sources: list[dict] = []
        hhmm = inp.as_of.astimezone(NY).strftime("%H%M")
        info_id, chain_id = f"src-nasdaq-{slug(p.symbol)}-info-{hhmm}", f"src-nasdaq-{slug(p.symbol)}-chain-{hhmm}"
        try:
            q = self.quotes.get_quote(p.symbol)
        except DataUnavailable as e:
            notes.append(f"{p.symbol}: quote unavailable ({e})")
            q = None
        pev: list[PEvent] = []
        if q is not None:
            fetched = q.fetched_at or inp.as_of
            sources.append({"id": info_id, "url": q.source or "urn:poodle:unknown", "fetched_at": fetched.isoformat(),
                            "kind": "quote_api", "publisher": "Nasdaq", "realtime": bool(q.is_realtime)})
            quote, volume = self._quote(q, info_id, fetched)
            if self.events is not None:
                try:
                    pev = self.events.get_events(p.symbol)
                except DataUnavailable:
                    pass
        else:
            m = lambda: MISS(note="quote provider unavailable")
            quote = {k: m() for k in ("last", "bid", "ask", "prev_close", "change_pct")}
            volume = {k: m() for k in ("volume", "avg_volume", "volume_vs_avg")}
        slices = []
        try:
            chain = self.chains.get_chain(p.symbol, q)
            fetched = chain.fetched_at or inp.as_of
            sources.append({"id": chain_id, "url": chain.source or "urn:poodle:unknown",
                            "fetched_at": fetched.isoformat(), "kind": "option_chain_api", "publisher": "Nasdaq",
                            "realtime": chain.quote_basis == QuoteBasis.REALTIME})
            slices = self._slices(chain, q, chain_id, fetched, inp)
        except DataUnavailable as e:
            notes.append(f"{p.symbol}: option chain unavailable ({e})")
        snap = {"run_id": inp.run_id, "quote": quote, "volume": volume,
                "iv": {"iv30_current": MISS(note="IV not in Nasdaq chain"),
                       "iv30_history": MISS(note="no IV history source")},
                "option_chain_slices": slices}
        return snap, sources, pev, notes

    @staticmethod
    def _quote(q: UnderlyingQuote, sid: str, fetched: datetime):
        t = _cap(q.quote_time, fetched)
        st = "ok" if q.is_realtime else "stale"
        quote = {"last": F(q.last, sid, t, st), "bid": F(q.bid, sid, t, st), "ask": F(q.ask, sid, t, st),
                 "prev_close": F(q.prev_close, sid, _cap(q.prev_close_time, fetched), "ok"),
                 "change_pct": F(q.pct_change, sid, t, st),
                 "week52_low": F(q.week52_low, sid, fetched, "ok"), "week52_high": F(q.week52_high, sid, fetched, "ok")}
        vol = {"volume": F(q.volume, sid, t, st, note=f"session-to-date ({q.market_status or 'status unknown'})"),
               "avg_volume": F(q.avg_volume, sid, fetched, "ok") if q.avg_volume else MISS(note="not obtained"),
               "volume_vs_avg": (derived(round(q.volume / q.avg_volume, 4), sid, t, st, "volume / avg_volume")
                                 if q.volume and q.avg_volume else MISS(note="needs avg_volume"))}
        return quote, vol

    @staticmethod
    def _slices(chain: OptionChain, q: Optional[UnderlyingQuote], sid: str, fetched: datetime, inp: ResearchInput):
        today = inp.as_of.astimezone(NY).date()
        spot = q.last if q and q.last else None
        prior = chain.quote_basis == QuoteBasis.PRIOR_CLOSE
        out = []
        for exp in chain.expiries:
            if not (0 <= (exp - today).days <= MAX_SLICE_DTE):
                continue
            contracts = []
            for oq in sorted((x for x in chain.quotes if x.expiry == exp), key=lambda x: (x.right.value, x.strike)):
                if spot and abs(oq.strike - spot) / spot * 100 > inp.strike_window_pct:
                    continue
                t = _cap(oq.quote_time, fetched)
                st = "stale" if prior or chain.quote_basis == QuoteBasis.UNKNOWN else "ok"
                note = PRIOR_NOTE if prior else ("quote time unknown" if st == "stale" else None)
                bid, ask = F(oq.bid, sid, t, st, note=note), F(oq.ask, sid, t, st, note=note)
                mid = (derived(round((oq.bid + oq.ask) / 2, 6), sid, t, st, "(bid+ask)/2")
                       if oq.bid is not None and oq.ask is not None else MISS(sid, "needs bid and ask"))
                g = MISS(sid, GREEK_NOTE)
                c = {"contract_id": occ_symbol(chain.underlying, exp, oq.right.value, oq.strike),
                     "type": "call" if oq.right.value == "C" else "put", "strike": oq.strike, "expiry": exp.isoformat(),
                     "style": "american", "multiplier": 100, "bid": bid, "ask": ask, "mid": mid,
                     "last": F(oq.last, sid, t, st, note=note), "iv": MISS(sid, GREEK_NOTE),
                     "open_interest": F(oq.open_interest, sid, t, "ok", note="OCC open interest as of prior close"),
                     "volume": F(oq.volume, sid, t, st, note=note),
                     "greeks": {"delta": g, "gamma": g, "theta": g, "vega": g}}
                contracts.append(c)
            if contracts:
                s = {"expiry": exp.isoformat(), "source_id": sid, "contracts": contracts}
                if chain.note:
                    s["slice_note"] = f"chain lastTrade: {chain.note}" + ("; quotes are prior-session" if prior else "")
                out.append(s)
        return out

    # ---- events / coverage ----
    @staticmethod
    def _events(inp: ResearchInput, provider_events: list[PEvent], sources: dict) -> list[dict]:
        out = [ResearchAgent._manual_event(e, inp.source_times.get(e.source_id, inp.as_of)) for e in inp.events]
        seen = {(e["kind"], e.get("underlying"), e["scheduled_at"]["value"][:10]) for e in out}
        hhmm = inp.as_of.astimezone(NY).strftime("%H%M")
        for e in provider_events:
            kind = "earnings" if e.kind.value == "earnings" else "other"
            if (kind, e.symbol, e.date.isoformat()) in seen:
                continue
            sid = f"src-nasdaq-{slug(e.symbol or 'mkt')}-info-{hhmm}"
            out.append({"event_id": f"ev-{slug(e.symbol or 'mkt')}-{kind}-{e.date.isoformat()}", "kind": kind,
                        "underlying": e.symbol,
                        "scheduled_at": (F(ny_at(e.date, "00:00").isoformat(), sid, inp.as_of, "ok",
                                           note="date only; timing unknown") if sid in sources
                                         else MISS(note="provider event without a recorded source")),
                        "timing": "unknown", "description": e.description})
            seen.add((kind, e.symbol, e.date.isoformat()))
        return out

    @staticmethod
    def _manual_event(e: ManualEvent, as_of: datetime) -> dict:
        t = e.as_of or as_of
        d: dict[str, Any] = {"event_id": e.event_id, "kind": e.kind, "underlying": e.underlying,
                             "scheduled_at": F(e.scheduled_at.astimezone(NY).isoformat(), e.source_id, t, "ok",
                                               note=e.note),
                             "timing": e.timing}
        if e.dividend_amount is not None:
            d["dividend_amount"] = F(e.dividend_amount, e.source_id, t, "ok")
        if e.description:
            d["description"] = e.description
        return d

    @staticmethod
    def _coverage(c: ManualCoverage, as_of: datetime) -> dict:
        if c.checked_through is None:
            ct = MISS(note=c.note)
        elif c.formula:
            ct = derived(c.checked_through, c.source_id, as_of, "ok", c.formula, note=c.note)
        else:
            ct = F(c.checked_through, c.source_id, as_of, "ok", note=c.note)
        return {"kind": c.kind, "underlying": c.underlying, "checked_through": ct}

    def summarize_input(self, inp: ResearchInput) -> dict[str, Any]:
        return {"picks": [p.symbol for p in inp.picks], "manual_events": len(inp.events),
                "coverage_items": len(inp.coverage)}

    def summarize_output(self, out: ResearchOutput) -> dict[str, Any]:
        return {"candidates": [c.underlying for c in out.candidates],
                "contracts": sum(len(s.contracts) for c in out.candidates for sn in c.snapshots
                                 for s in sn.option_chain_slices),
                "events": len(out.event_calendar.events), "notes": len(out.notes)}
