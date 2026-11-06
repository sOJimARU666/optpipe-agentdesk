"""Research: picks -> schema-v1 candidates (sourced quotes + chain slices) + event calendar.

Owner: Doodle (what to research) / Poodle (code). Never invents a value: anything a provider does
not give is a `missing` Field with a note.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from ..agent import Agent
from ..models.common import Event as PEvent, OptionChain, QuoteBasis, UnderlyingQuote
from ..models.contracts import ManualCoverage, ManualEvent, Pick, ResearchInput, ResearchOutput
from ..providers.base import DataUnavailable, EventProvider, OptionChainProvider, QuoteProvider
from ..providers.macro import MACRO_KINDS, CalendarSnapshot, MacroCalendarProvider, visible
from ..providers.treasury import RatePoint, RateProvider
from ..records.fields import F, MISS, NY, derived, ny_at, occ_symbol, slug
from .iv import BS_NOTE, ExpiryIv, IvHistoryStore, atm_iv, implied_vol, interpolate_iv30, year_fraction

MAX_SLICE_DTE = 60          # keep chain slices within ~2 months (record size)
PRIOR_NOTE = "prior-session close quote; options do not trade premarket"


def _publisher(url: Optional[str], default: str) -> str:
    u = str(url or "")
    return "Oodle pack" if u.startswith("pack:") or u.startswith("urn:optpipe:pack:") else default
GREEK_NOTE = "not provided by Nasdaq chain"
MACRO_HORIZON_DAYS = 120    # macro events kept in the record; coverage is capped to the same horizon


def fetched_t(contracts: list[dict]) -> datetime:
    """Quote instant of a slice (latest contract as_of)."""
    ts = [datetime.fromisoformat(c["bid"]["as_of"]) for c in contracts if c["bid"].get("as_of")]
    return max(ts)


def _cap(t: Optional[datetime], fetched: datetime) -> datetime:
    """as_of may never be later than fetched_at (INV-09)."""
    return fetched if t is None or t > fetched else t


class ResearchAgent(Agent[ResearchInput, ResearchOutput]):
    stage = "research"
    owner = "Doodle (logic) / Poodle (code)"
    input_model = ResearchInput
    output_model = ResearchOutput

    def __init__(self, quotes: QuoteProvider, chains: OptionChainProvider, events: Optional[EventProvider] = None,
                 macro: Optional[MacroCalendarProvider] = None, rates: Optional[RateProvider] = None,
                 iv_store: Optional[IvHistoryStore] = None, data_mode: str = "fixtures",
                 iv_target_days: int = 30, iv_min_history: int = 20):
        self.quotes, self.chains, self.events = quotes, chains, events
        self.macro, self.rates, self.iv_store, self.data_mode = macro, rates, iv_store, data_mode
        self.iv_target_days, self.iv_min_history = iv_target_days, iv_min_history
        self.rate: Optional[RatePoint] = None

    def run(self, inp: ResearchInput) -> ResearchOutput:
        cands, sources, notes, bias = [], {}, [], {}
        provider_events: list[PEvent] = []
        self.rate = None
        if self.rates is not None:
            try:
                self.rate = self.rates.get_rate(inp.as_of)
                sources[self.rate.source["id"]] = self.rate.source
                notes.append(f"risk-free rate for derived IV: {self.rate.formula} ({self.rate.source['id']})")
            except DataUnavailable as e:
                notes.append(f"risk-free rate unavailable ({e}); contract IV stays missing")
        macro_snaps: list[CalendarSnapshot] = []
        if self.macro is not None:
            got, n1 = self.macro.get_calendars(inp.as_of)
            macro_snaps, n2 = visible(got, inp.as_of)
            notes += n1 + n2
            for m in macro_snaps:
                sources[m.source["id"]] = m.source
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
        events, ev_notes = self._events(inp, provider_events, sources, macro_snaps)
        notes += ev_notes
        for c in cands:
            c["related_event_ids"] = [e["event_id"] for e in events if e.get("underlying") == c["underlying"]]
            if c["company_name"] is None:
                del c["company_name"]
            if c["exchange"] is None:
                del c["exchange"]
        coverage = self._macro_coverage(macro_snaps, inp.as_of)
        have = {(c["kind"], c["underlying"]) for c in coverage}
        for c in inp.coverage:
            if (c.kind, c.underlying) in have:
                notes.append(f"manual {c.kind} coverage superseded by the {c.kind} calendar source")
                continue
            coverage.append(self._coverage(c, inp.source_times.get(c.source_id or "", inp.as_of)))
        have = {(c["kind"], c["underlying"]) for c in coverage}
        for kind in MACRO_KINDS:
            if (kind, None) not in have:
                coverage.append({"kind": kind, "underlying": None,
                                 "checked_through": MISS(note=f"no {kind} calendar obtained for this run")})
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
                            "kind": "quote_api", "publisher": _publisher(q.source, "Nasdaq"),
                            "realtime": bool(q.is_realtime)})
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
                            "fetched_at": fetched.isoformat(), "kind": "option_chain_api",
                            "publisher": _publisher(chain.source, "Nasdaq"),
                            "realtime": chain.quote_basis == QuoteBasis.REALTIME})
            slices, per_exp, spot_note = self._slices(chain, q, chain_id, fetched, inp, self.rate)
            iv, iv_src, n = self._iv30(p.symbol, slices, per_exp, chain_id, inp, spot_note)
            sources += iv_src
            notes += n
        except DataUnavailable as e:
            notes.append(f"{p.symbol}: option chain unavailable ({e})")
            iv = None
        if iv is None:
            iv = {"iv30_current": MISS(note="no option chain"), "iv30_history": MISS(note="no option chain")}
        snap = {"run_id": inp.run_id, "quote": quote, "volume": volume, "iv": iv, "option_chain_slices": slices}
        return snap, sources, pev, notes

    # ---- derived IV ----
    def _iv30(self, symbol: str, slices: list[dict], per_exp: list[ExpiryIv], sid: str, inp: ResearchInput,
              spot_note: str):
        notes: list[str] = []
        srcs: list[dict] = []
        first = next((c["iv"] for s in slices for c in s["contracts"] if c["iv"]["status"] in ("ok", "stale")), None)
        value, how = interpolate_iv30(per_exp, self.iv_target_days) if per_exp else (None, "no usable contract IV")
        if value is None or first is None:
            cur = MISS(sid, f"IV30 not derivable: {how if self.rate else 'no risk-free rate'}")
        else:
            rid = self.rate.source["id"] if self.rate else "n/a"
            cur = derived(round(value, 6), sid, datetime.fromisoformat(first["as_of"]), first["status"],
                          f"ATM IV per expiry (call/put mean, linear in strike at spot) -> {how}",
                          note=f"{BS_NOTE}; spot {spot_note}; r from {rid}")
        obs_date = datetime.fromisoformat(first["as_of"]).astimezone(NY).date() if first else None
        if self.iv_store is None or obs_date is None:
            hist = MISS(note="no IV history store configured" if self.iv_store is None else "no current IV")
        else:
            from ..mdfeeds import history_reasons
            raw_dates = []
            for o in self.iv_store.load(symbol):
                try:
                    raw_dates.append(date.fromisoformat(str(o["date"])))
                except (KeyError, TypeError, ValueError):
                    continue
            # LOOKAHEAD / a naive clock must not be shown as a usable series. A short series that simply
            # does not reach the last completed session stays visible: R10 then says UNKNOWN (below 20 points)
            # or scores it. decision_reasons applies the full history_reasons check, including STALE-HISTORY,
            # when a caller passes the observation dates.
            blocked = [e for e in history_reasons(raw_dates, now=inp.as_of, what=f"{symbol}.iv_history")
                       if "MD-19-LOOKAHEAD" in e or "MD-19-TZ" in e or "MD-19-CALENDAR" in e]
            h = [] if blocked else self.iv_store.history(symbol, obs_date, inp.as_of)
            hid = f"src-optpipe-iv-history-{slug(symbol)}"
            if blocked:
                hist = MISS(note="; ".join(blocked))
            elif not h:
                hist = MISS(note=f"0 daily IV30 observations before {obs_date.isoformat()} "
                                 f"(R10 needs {self.iv_min_history}); history starts accumulating with this run")
            else:
                srcs.append({"id": hid, "url": f"urn:optpipe:state:{self.data_mode}/iv_history/{symbol.upper()}.jsonl",
                             "kind": "derived", "fetched_at": inp.as_of.isoformat(), "publisher": "optpipe",
                             "title": f"Derived daily IV30 history for {symbol.upper()}"})
                short = len(h) < self.iv_min_history
                hist = derived([v for _, v in h], hid, inp.as_of, "ok",
                               "daily IV30 (this pipeline's derived iv30_current), last per NY date, 30 calendar days",
                               note=f"{len(h)} observations {h[0][0].isoformat()}..{h[-1][0].isoformat()}"
                                    + (f" (< {self.iv_min_history}: R10 UNKNOWN)" if short else ""))
            if value is not None:
                self.iv_store.record(symbol, obs_date, value, inp.as_of, inp.run_id, self.data_mode)
                notes.append(f"{symbol}: IV30 {value:.4f} ({obs_date.isoformat()}) recorded to IV history")
        return {"iv30_current": cur, "iv30_history": hist}, srcs, notes

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
    def _slices(chain: OptionChain, q: Optional[UnderlyingQuote], sid: str, fetched: datetime, inp: ResearchInput,
                rate: Optional[RatePoint] = None):
        today = inp.as_of.astimezone(NY).date()
        spot = q.last if q and q.last else None
        prior = chain.quote_basis == QuoteBasis.PRIOR_CLOSE
        # IV must use the spot that matches the option quotes: prior-session quotes -> prior close.
        iv_spot, spot_note = (q.prev_close, "prior close (option quotes are prior-session)") if prior and q \
            and q.prev_close else (spot, "underlying last")
        out, per_exp = [], []
        for exp in chain.expiries:
            if not (0 <= (exp - today).days <= MAX_SLICE_DTE):
                continue
            contracts, pts = [], []
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
                     "last": F(oq.last, sid, t, st, note=note),
                     "iv": ResearchAgent._contract_iv(oq, exp, iv_spot, rate, t, sid, st, pts),
                     "open_interest": F(oq.open_interest, sid, t, "ok", note="OCC open interest as of prior close"),
                     "volume": F(oq.volume, sid, t, st, note=note),
                     "greeks": {"delta": g, "gamma": g, "theta": g, "vega": g}}
                contracts.append(c)
            if pts and iv_spot:
                a = atm_iv(iv_spot, pts)
                if a is not None:
                    per_exp.append(ExpiryIv(exp, year_fraction(fetched_t(contracts), ny_at(exp, "16:00")), a))
            if contracts:
                s = {"expiry": exp.isoformat(), "source_id": sid, "contracts": contracts}
                if chain.note:
                    s["slice_note"] = f"chain lastTrade: {chain.note}" + ("; quotes are prior-session" if prior else "")
                out.append(s)
        return out, per_exp, spot_note

    @staticmethod
    def _contract_iv(oq, exp: date, spot: Optional[float], rate: Optional[RatePoint], t: datetime, sid: str,
                     st: str, pts: list) -> dict:
        if rate is None:
            return MISS(sid, "no risk-free rate for derived IV")
        if spot is None or oq.bid is None or oq.ask is None or oq.ask <= 0:
            return MISS(sid, "derived IV needs spot, bid and ask")
        mid = (oq.bid + oq.ask) / 2
        tt = year_fraction(t, ny_at(exp, "16:00"))
        iv = implied_vol(oq.right.value, mid, spot, oq.strike, tt, rate.rate)
        if iv is None:
            return MISS(sid, "no Black-Scholes solution for the mid (outside no-arbitrage bounds)")
        pts.append((oq.strike, oq.right.value, iv))
        return derived(round(iv, 6), sid, t, st,
                       f"Black-Scholes IV from mid {mid:.4f}, S={spot}, K={oq.strike}, T={tt:.5f}y, r={rate.rate:.6f}",
                       note=f"{BS_NOTE}; r: {rate.source['id']}")

    # ---- events / coverage ----
    @staticmethod
    def _events(inp: ResearchInput, provider_events: list[PEvent], sources: dict,
                macro: Optional[list[CalendarSnapshot]] = None) -> tuple[list[dict], list[str]]:
        notes: list[str] = []
        horizon = inp.as_of.astimezone(NY).date() + timedelta(days=MACRO_HORIZON_DAYS)
        out: list[dict] = []
        for m in macro or []:
            sid, t = m.source["id"], m.fetched_at
            for e in m.events:
                if e.at.date() > horizon:
                    continue
                d = {"event_id": f"ev-{e.kind.lower()}-{e.at.date().isoformat()}", "kind": e.kind,
                     "underlying": None,
                     "scheduled_at": F(e.at.isoformat(), sid, t, "ok", note=e.note), "timing": "at_time",
                     "description": e.description}
                out.append(d)
        provided = {(e["kind"], e["scheduled_at"]["value"][:10]) for e in out}
        for e in inp.events:
            ev = ResearchAgent._manual_event(e, inp.source_times.get(e.source_id, inp.as_of))
            if e.underlying is None and (e.kind, ev["scheduled_at"]["value"][:10]) in provided:
                notes.append(f"manual event {e.event_id} superseded by the {e.kind} calendar source")
                continue
            out.append(ev)
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
        return out, notes

    @staticmethod
    def _macro_coverage(snaps: list[CalendarSnapshot], as_of: datetime) -> list[dict]:
        horizon = as_of.astimezone(NY).date() + timedelta(days=MACRO_HORIZON_DAYS)
        out = []
        for m in snaps:
            if m.checked_through is None:
                continue
            ct, formula = m.checked_through, m.formula
            if ct > horizon:
                ct, formula = horizon, f"min({formula}; {m.checked_through.isoformat()}, as_of + {MACRO_HORIZON_DAYS}d " \
                                       f"record horizon)"
            out.append({"kind": m.kind, "underlying": None,
                        "checked_through": derived(ct.isoformat(), m.source["id"], m.fetched_at, "ok", formula,
                                                   note="; ".join(m.notes) or None)})
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
                "coverage_items": len(inp.coverage), "macro_calendars": self.macro is not None,
                "rates": self.rates is not None, "iv_history": self.iv_store is not None}

    def summarize_output(self, out: ResearchOutput) -> dict[str, Any]:
        return {"candidates": [c.underlying for c in out.candidates],
                "contracts": sum(len(s.contracts) for c in out.candidates for sn in c.snapshots
                                 for s in sn.option_chain_slices),
                "events": len(out.event_calendar.events), "notes": len(out.notes)}
