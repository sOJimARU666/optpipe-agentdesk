"""Convert the 2026-10-13 sample session (../session-2026-10-13.json/.md + raw Nasdaq files)
into session.example.json conforming to session.schema.json. Risk reviews are COMPUTED by
risk_reference.py so the example is guaranteed consistent with risk_rules.yaml."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import risk_reference as rr
import validate_session

SPEC = Path(__file__).resolve().parent
RAW = SPEC.parent
NY, BERLIN = rr.NY, rr.BERLIN


def ny(s: str) -> str:
    """'2026-10-13 07:52' (NY wall clock) -> ISO with correct NY offset"""
    return datetime.fromisoformat(s).replace(tzinfo=NY).isoformat()


def tp(s: str) -> dict:
    return rr.tspair(datetime.fromisoformat(s).replace(tzinfo=NY))


def F(value, src, as_of, status="ok", **kw):
    d = {"value": value, "source_id": src, "as_of": ny(as_of) if as_of and "T" not in as_of else as_of, "status": status}
    d.update(kw)
    return d


def MISS(src=None, note=None):
    d = {"value": None, "source_id": src, "as_of": None, "status": "missing"}
    if note:
        d["note"] = note
    return d


def CONFLICT(*alts, note=None):
    d = {"value": None, "source_id": None, "as_of": None, "status": "conflicting",
         "conflicts": [{"value": v, "source_id": s, "as_of": ny(a) if a else None, **({"note": n} if n else {})} for v, s, a, n in alts]}
    if note:
        d["note"] = note
    return d


T_PRE, T_PREOPEN, T_POST = "2026-10-13 07:52", "2026-10-13 09:09", "2026-10-13 12:16"
PRIOR_CLOSE = "2026-10-12 16:00"

FV = "https://finviz.com/screener?v=111&f=sh_avgvol_o1000,sh_opt_option,sh_price_o10&s="
sources = [
    ("src-finviz-unusualvolume", FV + "ta_unusualvolume", T_PRE, "screener", "Finviz"),
    ("src-finviz-topgainers", FV + "ta_topgainers", T_PRE, "screener", "Finviz"),
    ("src-finviz-toplosers", FV + "ta_toplosers", T_PRE, "screener", "Finviz"),
    ("src-finviz-newhigh", FV + "ta_newhigh", T_PRE, "screener", "Finviz"),
    ("src-finviz-newlow", FV + "ta_newlow", T_PRE, "screener", "Finviz"),
    ("src-sec-jpm-q3", "https://www.sec.gov/Archives/edgar/data/19617/000162828026065582/jpm-20261013.htm", T_PRE, "filing", "SEC EDGAR"),
    ("src-newsquawk-jpm", "https://www.newsquawk.com/headlines/jpmorgan-chase--co-jpm-q3-2026-usd-eps-557-exp-593-revenue-502bln-exp-5123bln", T_PRE, "news", "Newsquawk"),
    ("src-wsj-jpm", "https://www.wsj.com/livecoverage/stock-market-today-bank-earnings-10-13-2026/card/jpm-shares-slump-pre-market-after-client-activity-moderates-AnWEiArdNSXmN1zrm7MH", T_PRE, "news", "WSJ"),
    ("src-tipranks-jpm", "https://www.tipranks.com/news/jpmorgan-stock-gains-as-higher-profits-offset-q3-earnings-miss", T_PRE, "news", "TipRanks"),
    ("src-morningstar-estimates", "https://www.morningstar.com/news/dow-jones/202610132115/north-american-morning-briefing-stock-futures-mixed-as-bank-earnings-kick-off-earnings-season", T_PRE, "news", "Morningstar/Dow Jones"),
    ("src-mainichi", "https://mainichi.jp/english/articles/20261013/p2g/00m/0bu/047000c", T_PRE, "news", "Mainichi"),
    ("src-ilsole", "https://en.ilsole24ore.com/art/kospi-plunges-74-per-cent-amid-pressure-from-china-chips-and-oil-prices-falling-AJIAnGiB", T_PRE, "news", "Il Sole 24 Ore"),
    ("src-alliance", "https://www.morningstar.com/news/alliance-news/1791878248964683800/london-market-open-europe-shakes-off-tech-decline-before-us-banks", T_PRE, "news", "Alliance News"),
    ("src-eme", "https://economymiddleeast.com/news/stock-market-today-nasdaq-falls-0-44-percent-kospi-sinks-5-5-percent-as-stoxx-600-gains-0-44-percent-on-ai-chip-selloff/", T_PRE, "news", "Economy Middle East"),
    ("src-nasdaq-jpm-info-0752", "https://api.nasdaq.com/api/quote/JPM/info?assetclass=stocks", T_PRE, "quote_api", "Nasdaq"),
    ("src-nasdaq-wdc-info-0752", "https://api.nasdaq.com/api/quote/WDC/info?assetclass=stocks", T_PRE, "quote_api", "Nasdaq"),
    ("src-nasdaq-jpm-chain-0752", "https://api.nasdaq.com/api/quote/JPM/option-chain?assetclass=stocks", T_PRE, "option_chain_api", "Nasdaq"),
    ("src-nasdaq-wdc-chain-0752", "https://api.nasdaq.com/api/quote/WDC/option-chain?assetclass=stocks", T_PRE, "option_chain_api", "Nasdaq"),
    ("src-wd-ir", "https://www.westerndigital.com/company/newsroom/press-releases/2026/2026-10-08-wd-to-announce-first-quarter-fiscal-year-2027-financial-results", T_PRE, "company_ir", "Western Digital"),
    ("src-finviz-wdc-news", "https://finviz.com/news/401543/western-digital-shares-fall-36-as-ubs-cuts-price-target-amid-hdd-supply-concerns", T_PRE, "news", "Finviz"),
    ("src-investing-wdc", "https://www.investing.com/news/stock-market-news/why-is-western-digital-stock-falling-today-93CH-4945879", T_PRE, "news", "Investing.com"),
    ("src-nasdaq-premarket-0909", "https://api.nasdaq.com/api/quote/JPM/info?assetclass=stocks", T_PREOPEN, "quote_api", "Nasdaq (JPM, WDC, GS, C, WFC info calls)"),
    ("src-stocktitan-gs", "https://www.stocktitan.net/sec-filings/GS/8-k-goldman-sachs-group-inc-reports-material-event-8b312bd35ddb.html", T_PREOPEN, "filing", "StockTitan (GS 8-K)"),
    ("src-citi-q3", "https://www.citigroup.com/rcs/citigpa/storage/public/Earnings/Q32026/2026prqtr3rslt.pdf", T_PREOPEN, "company_ir", "Citigroup"),
    ("src-wfc-q3", "https://www.wellsfargo.com/assets/pdf/about/investor-relations/earnings/third-quarter-2026-earnings.pdf", T_PREOPEN, "company_ir", "Wells Fargo"),
    ("src-bls-cpi", "https://www.bls.gov/schedule/news_release/cpi.htm", T_PREOPEN, "economic_calendar", "BLS"),
    ("src-jpm-ir-dividend", "https://www.jpmorganchase.com/ir/news/2026/jpmc-declares-increased-common-stock-dividend-9-15", T_PREOPEN, "company_ir", "JPMorganChase IR"),
    ("src-nasdaq-jpm-info-1216", "https://api.nasdaq.com/api/quote/JPM/info?assetclass=stocks", T_POST, "quote_api", "Nasdaq"),
    ("src-nasdaq-jpm-chain-1216", "https://api.nasdaq.com/api/quote/JPM/option-chain?assetclass=stocks", T_POST, "option_chain_api", "Nasdaq"),
    ("src-nasdaq-wdc-info-1216", "https://api.nasdaq.com/api/quote/WDC/info?assetclass=stocks", T_POST, "quote_api", "Nasdaq"),
]
SRC = [{"id": i, "url": u, "fetched_at": ny(t), "kind": k, "publisher": p} for i, u, t, k, p in sources]
for s in SRC:
    if s["kind"] == "news" and s["publisher"] not in ("Finviz",):
        s["notes"] = "fetched_at approximated by the run time (sample did not log per-URL fetch times)"
    if s["kind"] == "quote_api":
        s["realtime"] = True
    if s["kind"] == "option_chain_api":
        s["realtime"] = False

# ---------------------------------------------------------------- scan
def screen(name, status, rows, reason=None):
    d = {"screen_id": name.replace("ta_", "scr-"), "name": name, "source_id": f"src-finviz-{name[3:]}",
         "filters": "sh_avgvol_o1000,sh_opt_option,sh_price_o10", "fetched_at": ny(T_PRE), "status": status, "rows": rows,
         "parse_notes": "Ticker first letter was doubled in raw parse (e.g. WWDC=WDC); corrected. Page not timestamped; values look prior-session/premarket -> rows marked stale with as_of = fetch time."}
    if reason:
        d["status_reason"] = reason
    return d

def rowsf(name, items):
    out = []
    for i, it in enumerate(items, 1):
        r = {"rank": i, "ticker": it[0]}
        if len(it) > 1:
            r["last"] = F(it[1], f"src-finviz-{name[3:]}", T_PRE, "stale")
        if len(it) > 2:
            r["change_pct"] = F(it[2], f"src-finviz-{name[3:]}", T_PRE, "stale")
        out.append(r)
    return out

screens = [
    screen("ta_unusualvolume", "missing", [], "page returned no result rows"),
    screen("ta_topgainers", "missing", [], "page returned no result rows"),
    screen("ta_toplosers", "stale", rowsf("ta_toplosers", [("SNDX", 14.81, -10.51), ("S", 23.56, -8.18), ("FUN", 11.00, -7.72), ("WDC", 363.52, -6.05), ("BNTX", 90.15, -5.94), ("SKHY", 153.26, -5.53), ("STX", 711.05, -5.60)])),
    screen("ta_newhigh", "stale", rowsf("ta_newhigh", [("ZIM", 31.03), ("V", 396.09), ("FRO", 58.93), ("DHT", 25.95), ("OSCR", 34.46)])),
    screen("ta_newlow", "stale", rowsf("ta_newlow", [(t,) for t in "NAMS FUN OKLO QNT ACAD CBRS XE BIDU NNE FRVO".split()])),
]

earnings = [
    {"run_id": "run-premarket", "ticker": "JPM", "fiscal_period": "Q3 2026",
     "reported": F(True, "src-sec-jpm-q3", T_PRE),
     "eps_actual": F(5.57, "src-sec-jpm-q3", T_PRE, note="diluted"),
     "eps_estimate": F(5.93, "src-newsquawk-jpm", T_PRE),
     "revenue_actual_usd_bn": F(50.172, "src-sec-jpm-q3", T_PRE),
     "revenue_estimate_usd_bn": F(51.23, "src-newsquawk-jpm", T_PRE),
     "premarket_change_pct": CONFLICT((-3.7, "src-wsj-jpm", "2026-10-13 07:03", "~$322"), (0.78, "src-nasdaq-jpm-info-0752", T_PRE, "$336.91 realtime"), (2.0, "src-tipranks-jpm", T_PRE, "~+2%, time not stated")),
     "extra_metrics": {"nii_usd_bn": F(27.036, "src-sec-jpm-q3", T_PRE)}},
]
for tk, est in (("GS", 12.86), ("C", 2.65), ("WFC", 1.85)):
    earnings.append({"run_id": "run-premarket", "ticker": tk, "fiscal_period": "Q3 2026", "reported": MISS(note="not found in searches"),
                     "eps_actual": MISS(), "eps_estimate": F(est, "src-morningstar-estimates", T_PRE),
                     "revenue_actual_usd_bn": MISS(), "revenue_estimate_usd_bn": MISS(), "premarket_change_pct": MISS()})
for tk, src, eps, est, rev, pm in (("GS", "src-stocktitan-gs", 17.01, 12.86, 18.68, 4.26), ("C", "src-citi-q3", 2.85, 2.65, 23.8, 0.45), ("WFC", "src-wfc-q3", 1.87, 1.85, 21.87, -2.81)):
    earnings.append({"run_id": "run-preopen", "ticker": tk, "fiscal_period": "Q3 2026", "reported": F(True, src, T_PREOPEN),
                     "eps_actual": F(eps, src, T_PREOPEN), "eps_estimate": F(est, "src-morningstar-estimates", T_PRE),
                     "revenue_actual_usd_bn": F(rev, src, T_PREOPEN), "revenue_estimate_usd_bn": MISS(),
                     "premarket_change_pct": F(pm, "src-nasdaq-premarket-0909", "2026-10-13 09:08")})
earnings.append({"run_id": "run-preopen", "ticker": "JPM", "fiscal_period": "Q3 2026", "reported": F(True, "src-sec-jpm-q3", T_PRE),
                 "eps_actual": F(5.57, "src-sec-jpm-q3", T_PRE), "eps_estimate": F(5.93, "src-newsquawk-jpm", T_PRE),
                 "revenue_actual_usd_bn": F(50.172, "src-sec-jpm-q3", T_PRE), "revenue_estimate_usd_bn": F(51.23, "src-newsquawk-jpm", T_PRE),
                 "premarket_change_pct": F(1.17, "src-nasdaq-premarket-0909", "2026-10-13 09:08", note="premarket conflict resolved toward positive")})

# ---------------------------------------------------------------- global
inf = "source attribution inferred from the sample's block-level source list"
gi = lambda sym, name, region, level, chg, state: {"symbol": sym, "name": name, "region": region, "level": level, "change_pct": chg, "session_state": state}
instruments = [
    gi("N225", "Nikkei 225", "asia_pacific", F(67777.52, "src-mainichi", T_PRE), F(-1.82, "src-mainichi", T_PRE), "close"),
    gi("TOPIX", "TOPIX", "asia_pacific", F(4110.67, "src-mainichi", T_PRE, note=inf), F(0.14, "src-mainichi", T_PRE, note=inf), "close"),
    gi("KOSPI", "KOSPI", "asia_pacific", CONFLICT((5914.35, "src-ilsole", None, "close"), (6035.35, "src-eme", None, None)), CONFLICT((-7.4, "src-ilsole", None, "headline 7.4%; sample noted ~-7%"), (-5.5, "src-eme", None, None)), "close"),
    gi("HSI", "Hang Seng", "asia_pacific", F(24033.51, "src-ilsole", T_PRE), CONFLICT((-1.0, "src-ilsole", None, None), (0.2, "src-alliance", None, None)), "close"),
    gi("SHCOMP", "Shanghai Composite", "asia_pacific", F(3754.82, "src-ilsole", T_PRE), CONFLICT((-1.1, "src-ilsole", None, None), (0.1, "src-alliance", None, None)), "close"),
    gi("TAIEX", "Taiex", "asia_pacific", MISS(), F(-1.9, "src-ilsole", T_PRE, note=inf), "close"),
    gi("AS51", "ASX 200", "asia_pacific", F(8767.40, "src-ilsole", T_PRE, note=inf), F(-0.2, "src-ilsole", T_PRE, note=inf), "close"),
    gi("SXXP", "STOXX 600", "europe", F(638.44, "src-eme", "2026-10-13T07:14:00+00:00"), F(0.44, "src-eme", "2026-10-13T07:14:00+00:00"), "early"),
    gi("DAX", "DAX", "europe", MISS(), F(0.2, "src-alliance", T_PRE, note=inf), "early"),
    gi("CAC", "CAC 40", "europe", MISS(), F(0.3, "src-alliance", T_PRE, note=inf), "early"),
    gi("UKX", "FTSE 100", "europe", MISS(), F(1.0, "src-alliance", T_PRE, note="approximate (~+1%); " + inf), "early"),
]
for i in instruments:  # Field.as_of for STOXX given in UTC: keep, it carries its own offset
    pass

# ---------------------------------------------------------------- events
events = [
    {"event_id": "ev-jpm-earn-q3", "kind": "earnings", "underlying": "JPM", "timing": "bmo",
     "scheduled_at": F(ny("2026-10-13 00:00"), "src-sec-jpm-q3", T_PRE), "description": "JPM Q3 2026 results (already reported before the open)"},
    {"event_id": "ev-wdc-earn-q1fy27", "kind": "earnings", "underlying": "WDC", "timing": "amc",
     "scheduled_at": F(ny("2026-10-22 00:00"), "src-wd-ir", T_PRE), "description": "WDC fiscal Q1 2027 results after close"},
    {"event_id": "ev-cpi-2026-10-14", "kind": "CPI", "underlying": None, "timing": "at_time",
     "scheduled_at": F(ny("2026-10-14 08:30"), "src-bls-cpi", T_PREOPEN), "description": "CPI (September data)"},
    {"event_id": "ev-ppi-2026-10-15", "kind": "PPI", "underlying": None, "timing": "at_time",
     "scheduled_at": F(ny("2026-10-15 08:30"), "src-bls-cpi", T_PREOPEN, note="sample recorded the PPI date next to CPI without a separate citation; verify at bls.gov/schedule/news_release/ppi.htm"), "description": "PPI"},
    {"event_id": "ev-jpm-exdiv-2026-10-06", "kind": "ex_dividend", "underlying": "JPM", "timing": "unknown",
     "scheduled_at": F(ny("2026-10-06 00:00"), "src-jpm-ir-dividend", T_PREOPEN, note="sample said '10-05/06'; with T+1 settlement ex-date = record date (10-06); verify"),
     "dividend_amount": F(1.65, "src-jpm-ir-dividend", T_PREOPEN), "description": "$1.65 payable 2026-10-31"},
]
coverage = [
    {"kind": "earnings", "underlying": "JPM", "checked_through": F("2026-12-31", "src-sec-jpm-q3", T_PRE, method="derived", formula="Q3 reported 2026-10-13; quarterly cadence -> next report not before January 2027")},
    {"kind": "earnings", "underlying": "WDC", "checked_through": F("2026-12-31", "src-wd-ir", T_PRE, method="derived", formula="next report announced for 2026-10-22; following one ~3 months later")},
    {"kind": "ex_dividend", "underlying": "JPM", "checked_through": F("2026-12-31", "src-jpm-ir-dividend", T_PREOPEN, method="derived", formula="Q4 dividend ex-date 2026-10-06 passed; next quarterly ex-date expected January 2027")},
    {"kind": "ex_dividend", "underlying": "WDC", "checked_through": MISS(note="not checked")},
    {"kind": "CPI", "underlying": None, "checked_through": F("2026-12-31", "src-bls-cpi", T_PREOPEN, note="BLS annual release schedule")},
    {"kind": "PPI", "underlying": None, "checked_through": MISS(note="PPI schedule page not opened; only one date noted")},
    {"kind": "FOMC", "underlying": None, "checked_through": MISS(note="not checked in sample")},
    {"kind": "NFP", "underlying": None, "checked_through": MISS(note="not checked in sample")},
]

# ---------------------------------------------------------------- candidates
def chain_contracts(tk, picks, src, as_of, status, from_raw=True):
    raw = json.loads((RAW / f"oc_{tk}.json").read_text())["data"]["table"]["rows"]
    out = []
    for strike, typ, *override in picks:
        p = "c_" if typ == "call" else "p_"
        if override:
            bid, ask, oi, vol, last = override[0]
        else:
            row = next(r for r in raw if r["expiryDate"] == "Oct 30" and r["strike"] == f"{strike:.2f}")
            num = lambda v: None if v in (None, "--", "") else float(v.replace(",", ""))
            bid, ask, oi, vol, last = num(row[p + "Bid"]), num(row[p + "Ask"]), num(row[p + "Openinterest"]), num(row[p + "Volume"]), num(row[p + "Last"])
        oi = None if oi is None else int(oi)  # v1.1: open_interest is an IntegerField (contracts)
        cid = f"{tk}261030{'C' if typ == 'call' else 'P'}{int(strike * 1000):08d}"
        stale_note = "prior-session close quote; options do not trade premarket" if status == "stale" else None
        q = lambda v: (F(v, src, as_of, status, **({"note": stale_note} if stale_note else {})) if v is not None else MISS(src))
        oi_f = F(oi, src, PRIOR_CLOSE if status == "stale" else as_of, "ok", note="OCC open interest as of prior close") if oi is not None else MISS(src)
        out.append({"contract_id": cid, "type": typ, "strike": float(strike), "expiry": "2026-10-30", "style": "american", "multiplier": 100,
                    "bid": q(bid), "ask": q(ask), "mid": F(round((bid + ask) / 2, 4), src, as_of, status, method="derived", formula="(bid+ask)/2"),
                    "last": q(last), "iv": MISS(src, "not provided by Nasdaq chain"), "open_interest": oi_f, "volume": q(vol),
                    "greeks": {g: MISS(src, "not provided by Nasdaq chain") for g in ("delta", "gamma", "theta", "vega")}})
    return out

def quote(src, at, last, chg, prev_close, bid=None, ask=None, lo=None, hi=None, w52=None, prev_src=None):
    q = {"last": F(last, src, at), "bid": F(bid, src, at) if bid is not None else MISS(), "ask": F(ask, src, at) if ask is not None else MISS(),
         "prev_close": F(prev_close, prev_src or src, PRIOR_CLOSE), "change_pct": F(chg, src, at)}
    if lo is not None:
        q["day_low"], q["day_high"] = F(lo, src, at), F(hi, src, at)
    if w52:
        q["week52_low"], q["week52_high"] = F(w52[0], src, at), F(w52[1], src, at)
    return q

VOL_MISS = lambda v, src, at: {"volume": F(v, src, at, note="premarket volume") if v is not None else MISS(), "avg_volume": MISS(note="not obtained"), "volume_vs_avg": MISS(note="needs avg_volume")}
IV_MISS = {"iv30_current": MISS(note="IV not in Nasdaq chain"), "iv30_history": MISS(note="no IV history source")}

jpm_pre_src, jpm_chain_pre = "src-nasdaq-jpm-info-0752", "src-nasdaq-jpm-chain-0752"
candidates = [
    {"candidate_id": "cand-jpm", "underlying": "JPM", "company_name": "JPMorgan Chase & Co.", "exchange": "NYSE",
     "catalysts": [F("Q3 EPS 5.57 vs 5.93 est and revenue 50.2bn vs 51.23bn (miss); NII 27.0bn beat", "src-newsquawk-jpm", T_PRE),
                   F("Premarket reversal from about -3.7% to positive", "src-nasdaq-jpm-info-0752", T_PRE)],
     "related_event_ids": ["ev-jpm-earn-q3", "ev-jpm-exdiv-2026-10-06"],
     "snapshots": [
        {"run_id": "run-premarket", "quote": quote(jpm_pre_src, T_PRE, 336.91, 0.78, 334.30, 336.68, 337.50, w52=(279.10, 366.50)),
         "volume": VOL_MISS(651371, jpm_pre_src, T_PRE), "iv": IV_MISS,
         "option_chain_slices": [{"expiry": "2026-10-30", "source_id": jpm_chain_pre, "slice_note": "chain lastTrade 337.05 inconsistent with realtime 336.91; quotes are prior-session",
                                  "contracts": chain_contracts("JPM", [(340, "call"), (345, "call"), (350, "call"), (355, "call"), (330, "put"), (325, "put")], jpm_chain_pre, PRIOR_CLOSE, "stale")}]},
        {"run_id": "run-preopen", "quote": quote("src-nasdaq-premarket-0909", "2026-10-13 09:08", 338.22, 1.17, 334.30, prev_src=jpm_pre_src),
         "volume": VOL_MISS(None, None, None), "iv": IV_MISS, "option_chain_slices": []},
        {"run_id": "run-postopen", "quote": quote("src-nasdaq-jpm-info-1216", T_POST, 335.56, 0.38, 334.30, lo=331.19, hi=344.35, prev_src=jpm_pre_src),
         "volume": VOL_MISS(None, None, None), "iv": IV_MISS,
         "option_chain_slices": [{"expiry": "2026-10-30", "source_id": "src-nasdaq-jpm-chain-1216",
                                  "contracts": chain_contracts("JPM", [(340, "call", (4.80, 5.55, 472, 330, None)), (350, "call", (1.81, 2.32, 747, 141, None))], "src-nasdaq-jpm-chain-1216", T_POST, "ok")}]}]},
    {"candidate_id": "cand-wdc", "underlying": "WDC", "company_name": "Western Digital Corporation", "exchange": "NASDAQ-GS",
     "catalysts": [F("Memory/storage selloff after Chinese CXMT chip news", "src-investing-wdc", T_PRE), F("UBS price-target cut on HDD supply", "src-finviz-wdc-news", T_PRE),
                   F("Finviz top loser", "src-finviz-toplosers", T_PRE, "stale")],
     "related_event_ids": ["ev-wdc-earn-q1fy27"],
     "snapshots": [
        {"run_id": "run-premarket", "quote": quote("src-nasdaq-wdc-info-0752", T_PRE, 363.40, -6.08, 386.94, 363.20, 363.50, w52=(112.52, 799.87)),
         "volume": VOL_MISS(243277, "src-nasdaq-wdc-info-0752", T_PRE), "iv": IV_MISS,
         "option_chain_slices": [{"expiry": "2026-10-30", "source_id": "src-nasdaq-wdc-chain-0752", "slice_note": "priced off prior close 386.94, not 363.40",
                                  "contracts": chain_contracts("WDC", [(360, "put"), (350, "put")], "src-nasdaq-wdc-chain-0752", PRIOR_CLOSE, "stale")}]},
        {"run_id": "run-preopen", "quote": quote("src-nasdaq-premarket-0909", "2026-10-13 09:08", 357.84, -7.52, 386.94, prev_src="src-nasdaq-wdc-info-0752"),
         "volume": VOL_MISS(None, None, None), "iv": IV_MISS, "option_chain_slices": []},
        {"run_id": "run-postopen", "quote": quote("src-nasdaq-wdc-info-1216", T_POST, 377.95, -2.32, 386.94, prev_src="src-nasdaq-wdc-info-0752"),
         "volume": VOL_MISS(None, None, None), "iv": IV_MISS, "option_chain_slices": []}]},
]

# ---------------------------------------------------------------- ticket
cond = lambda m, op, v, unit=None: {"metric": m, "op": op, "value": v, **({"unit": unit} if unit else {})}
# v1.1 raw tick fields (INV-13; JPM is penny-pilot: 0.01 below $3, 0.05 at/above). Debit limit DOWN, stop UP, target DOWN.
tcond = lambda m, op, v, raw, tick, rnd: {**cond(m, op, v), "raw_value": raw, "tick_size": tick, "rounding": rnd}
LIMIT_RAW, STOP_RAW, TARGET_RAW = 3.100625, 1.5125, 6.05   # ref mid 3.025 x 1.025 cap; 50% of mid; 2 x mid
ticket = {
    "ticket_id": "tkt-jpm-bcs-340-350", "candidate_id": "cand-jpm", "label": "HYPOTHETICAL - paper only, not an order", "created": tp(T_PRE),
    "underlying": "JPM", "strategy": "bull_call_debit_spread", "direction": "bullish",
    "thesis": "Premarket reversal after Q3 miss/NII beat; defined-risk upside to 350.",
    "legs": [{"leg_id": "leg-1", "contract_id": "JPM261030C00340000", "type": "call", "side": "buy", "strike": 340.0, "expiry": "2026-10-30", "ratio": 1, "multiplier": 100},
             {"leg_id": "leg-2", "contract_id": "JPM261030C00350000", "type": "call", "side": "sell", "strike": 350.0, "expiry": "2026-10-30", "ratio": 1, "multiplier": 100}],
    "quantity": 1,
    "limit_price": {"amount": 3.10, "price_type": "debit", "valid_only_if": "re-quoted live after 09:45 ET with each leg spread <= 10% of mid; sample's reference limit was 3.03 (mid), cap 3.10",
                    "raw_amount": LIMIT_RAW, "tick_size": 0.05, "rounding": "down"},
    "pricing": {"run_id": "run-premarket", "reference_mid": 3.025, "natural": 3.50, "status": "stale", "note": "UNPRICEABLE premarket: prior-session option quotes; tick rounding (penny-pilot JPM): limit 3.100625 -> 3.10 (down, 0.05), stop 1.5125 -> 1.52 (up, 0.01), target 6.05 -> 6.05 (down, 0.05)"},
    "risk_basis": "Per-trade max loss $500 is a PLACEHOLDER pending Eugene",
    "hold_window": {"start": tp("2026-10-13 09:45"), "end": tp("2026-10-27 16:00")},
    "entry_rules": [{"rule_id": "entry-1", "text": "After 09:45 ET (15:45 CEST), only if JPM > 334.30 (prev close), live net mid debit <= 3.10 and each leg spread <= 10% of mid",
                     "combine": "all", "conditions": [cond("time_ny", "gte", "09:45"), cond("underlying_last", "gt", 334.30), tcond("net_debit_mid", "lte", 3.10, LIMIT_RAW, 0.05, "down"), cond("leg_spread_pct_of_mid", "lte", 10, "pct")]}],
    "exit_rules": [{"rule_id": "exit-time", "text": "Time stop: close by 2026-10-27", "conditions": [cond("date_ny", "gte", "2026-10-27")]}],
    "stop_rules": [{"rule_id": "stop-1", "text": "Exit if JPM closes below 330 or spread value <= 1.52 (~50% of ref mid, rounded up)", "combine": "any",
                    "conditions": [cond("underlying_close", "lt", 330), tcond("position_value", "lte", 1.52, STOP_RAW, 0.01, "up")]}],
    "target_rules": [{"rule_id": "target-1", "text": "Close at spread value 6.05 (2x ref mid, rounded down) or JPM >= 350", "combine": "any",
                      "conditions": [tcond("position_value", "gte", 6.05, TARGET_RAW, 0.05, "down"), cond("underlying_last", "gte", 350)]}],
    "max_loss": {"amount_usd": 310.0, "unlimited": False, "at_price": 3.10, "basis": "1 x 3.10 x 100 at limit; sample quoted $302.50 at ref mid 3.025 and $350 at natural 3.50"},
    "max_profit": {"amount_usd": 690.0, "unlimited": False, "at_price": 3.10, "basis": "(10 width - 3.10) x 100; $697.50 at ref mid"},
    "breakevens": [{"underlying_price": 343.10, "at_price": 3.10}, {"underlying_price": 343.025, "at_price": 3.025}],
    "intended_event_ids": [],
    "dm_alternatives": [
        {"alt_id": "alt-jpm-bcs", "kind": "trade", "description": "JPM Oct30 340/350 bull call debit spread (this ticket)", "ticket_id": "tkt-jpm-bcs-340-350", "decision": "selected", "reason": "Best candidate, but only if live quotes pass risk"},
        {"alt_id": "alt-wdc-bps", "kind": "trade", "description": "WDC Oct30 360/350 bear put spread (ref mid 2.875)", "ticket_id": None, "decision": "rejected", "reason": "Earnings 2026-10-22 inside hold window; chain priced off stale 386.94; 350P spread ~12%"},
        {"alt_id": "alt-jpm-bps", "kind": "trade", "description": "JPM Oct30 330/325 bear put spread (fade the miss)", "ticket_id": None, "decision": "rejected", "reason": "325P OI 321 < 500; directional conflict with premarket reversal"},
        {"alt_id": "alt-no-trade", "kind": "no_trade", "description": "NO TRADE", "ticket_id": None, "decision": "preferred", "reason": "Stale option data and conflicting JPM premarket prints"}],
}

watch = lambda wid, kind, conds, sev, act, note: {"watch_id": wid, "target": {"ticket_id": ticket["ticket_id"], "position_id": None}, "kind": kind, "conditions": conds,
                                                  "severity": sev, "action": act, "active": False, "schedule": None, "note": note}
watchers = [
    watch("w-value", "option_value", [tcond("position_value", "lte", 1.52, STOP_RAW, 0.01, "up"), tcond("position_value", "gte", 6.05, TARGET_RAW, 0.05, "down")], "action", "suggest_close", "stop 1.52 / target 6.05"),
    watch("w-under", "underlying_price", [cond("underlying_last", "lt", 330), cond("underlying_last", "gt", 350)], "warn", "alert", "JPM <330 or >350"),
    watch("w-be", "underlying_price", [cond("underlying_last", "cross_above", 343.10)], "info", "alert", "breakeven at limit"),
    watch("w-iv", "iv_change", [cond("iv_change_pct", "gte", 20, "pct"), cond("iv_change_pct", "lte", -20, "pct")], "warn", "alert", "relative to entry IV (entry IV MISSING)"),
    watch("w-dte", "dte", [cond("dte", "lte", 5)], "warn", "alert", "5 DTE = 2026-10-25"),
    watch("w-time", "time", [cond("date_ny", "gte", "2026-10-27")], "action", "force_close_paper", "time stop"),
    watch("w-assign", "assignment", [cond("short_leg_itm", "eq", True), cond("ex_dividend_in_window", "eq", True)], "action", "alert", "short 350C ITM with <5 DTE or ex-div in window"),
]

H = lambda st, at, actor, reason, refs=None: {"status": st, "at": tp(at), "actor": actor, "reason": reason, **({"ref_ids": refs} if refs else {})}
idea_log = [
    {"idea_id": "idea-jpm-bcs", "description": "JPM Oct30 340/350 call spread", "ticket_id": "tkt-jpm-bcs-340-350", "current_status": "vetoed",
     "history": [H("proposed", T_PRE, "decision_maker", "Premarket reversal after earnings"),
                 H("vetoed", T_PRE, "risk_officer", "Market closed / stale quotes, 350C spread 20.8%, 340C OI 472, ex-div unknown", ["rv-premarket"]),
                 H("vetoed", T_POST, "risk_officer", "340C OI 472; both legs > 10% spread; mid 3.11 > 3.10", ["rv-postopen"])]},
    {"idea_id": "idea-wdc-bps", "description": "WDC Oct30 360/350 put spread", "ticket_id": None, "current_status": "rejected",
     "history": [H("proposed", T_PRE, "decision_maker", "Top loser on memory selloff"), H("rejected", T_PRE, "decision_maker", "Earnings Oct 22 inside hold; stale chain vs -6% gap; 350P spread 12%")]},
    {"idea_id": "idea-jpm-bps", "description": "JPM Oct30 330/325 put spread", "ticket_id": None, "current_status": "rejected",
     "history": [H("proposed", T_PRE, "decision_maker", "Bearish fade of the miss"), H("rejected", T_PRE, "decision_maker", "325P OI 321; directional conflict")]},
    {"idea_id": "idea-no-trade", "description": "No trade", "ticket_id": None, "current_status": "approved_pending",
     "history": [H("proposed", T_PRE, "decision_maker", "Stale data, conflicting prints"),
                 H("approved_pending", T_PRE, "decision_maker", "Current recommendation; awaiting Eugene"),
                 H("approved_pending", T_POST, "decision_maker", "Re-confirmed after post-open requote: stand down")]},
]

runs = [
    {"run_id": "run-premarket", "phase": "premarket", "started": tp(T_PRE), "market_status": F("Pre-Market", "src-nasdaq-jpm-info-0752", T_PRE), "summary": "Scan, candidates, ticket, review -> VETO"},
    {"run_id": "run-preopen", "phase": "preopen", "started": tp(T_PREOPEN), "market_status": F("Pre-Market", "src-nasdaq-premarket-0909", "2026-10-13 09:08"), "summary": "Bank actuals, CPI/ex-div found; still NO TRADE"},
    {"run_id": "run-postopen", "phase": "postopen", "started": tp(T_POST), "market_status": F("Open", "src-nasdaq-jpm-info-1216", T_POST), "summary": "Live requote; review -> VETO; NO TRADE"},
]

sess = {
    "schema_version": "1.1.2",
    "session": {"id": "sess-2026-10-13", "date": "2026-10-13", "mode": "paper", "created": tp(T_PRE), "updated": tp(T_POST),
                "risk_ruleset_version": rr.load_rules()["ruleset_version"],
                "market_calendar": {"timezone": "America/New_York", "is_trading_day": True, "early_close": False,
                                    "regular_open": tp("2026-10-13 09:30"), "regular_close": tp("2026-10-13 16:00"), "ny_berlin_offset_hours": 6},
                "notes": ["Converted from session-2026-10-13.json/.md (sample).", "Paper only: no broker, no live orders."]},
    "sources": SRC, "runs": runs,
    "premarket_scan": {"run_id": "run-premarket", "screens": screens, "earnings_reports": earnings},
    "global_snapshot": {"run_id": "run-premarket", "instruments": instruments,
                        "themes": [F("AI/memory-chip selloff (CXMT news), lower oil, bank earnings", "src-eme", T_PRE)]},
    "event_calendar": {"events": events, "coverage": coverage},
    "candidates": candidates, "tickets": [ticket], "risk_reviews": [], "approvals": [], "paper_positions": [],
    "watcher_rules": watchers, "alerts": [], "idea_log": idea_log,
    "data_quality": {"missing": [], "stale": [], "conflicting": [], "notes": []},
}

# v1.1 per-run arrays (INV-12): only the premarket run scanned / snapshotted; singular kept for v1 readers
sess["premarket_scans"] = [sess["premarket_scan"]]
sess["global_snapshots"] = [sess["global_snapshot"]]

# ---------------------------------------------------------------- reviews (computed)
rules = rr.load_rules()
for rid, run, at in (("rv-premarket", "run-premarket", T_PRE), ("rv-postopen", "run-postopen", T_POST)):
    inp = rr.inputs_from_session(sess, ticket["ticket_id"], run, ny(at))
    out = rr.evaluate(inp, rules)
    results = []
    for r in out["results"]:
        r = {k: v for k, v in r.items() if k in ("rule_id", "severity", "result", "measured", "threshold", "reason", "placeholder", "per_leg")}
        results.append(r)
    ov = out["overall"]
    ov["summary"] = f"{ov['verdict']}: blocking {', '.join(ov['blocking_rule_ids']) or 'none'}; flags {', '.join(ov['flag_rule_ids']) or 'none'}. Approval required regardless."
    sess["risk_reviews"].append({"review_id": rid, "ticket_id": ticket["ticket_id"], "run_id": run, "evaluated_at": tp(at),
                                 "ruleset_version": rules["ruleset_version"], "market_open_at_evaluation": out["market_open_at_evaluation"],
                                 "results": results, "overall": ov})

dq = validate_session.quality_index(sess)
sess["data_quality"] = {**dq, "notes": [
    "Sample missing_data: Finviz unusual volume & top gainers empty; avg volume JPM/WDC; IV for all strikes; live option quotes premarket; GS/C/WFC actuals (found at preopen); JPM ex-div (found at preopen); CPI date (found at preopen); conflicting Asia indices and JPM premarket prints.",
    "No paper position: every review VETO. No approvals exist."]}

out_path = SPEC / "session.example.json"
out_path.write_text(json.dumps(sess, indent=2, ensure_ascii=False) + "\n")
errs = validate_session.validate(sess)
print("\n".join(errs) if errs else f"wrote {out_path} - valid")
for rv in sess["risk_reviews"]:
    print(rv["review_id"], rv["overall"]["verdict"], [(r["rule_id"], r["result"]) for r in rv["results"]])
