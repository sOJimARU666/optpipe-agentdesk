"""Build the hermetic spec 1.1.8 market-data fixtures under fixtures/market_data/ deterministically.
ALL VALUES ARE FICTIONAL EXAMPLE DATA (data_label EXAMPLE, sources on example.com / example.org). They are not market
data and must never be read as such. test_market_snapshot_v1_1_8.py checks the vendored files equal build() output.
The 1.1.7 cases are built by build_market_data_fixtures_v1_1_7.py into fixtures/market_data_v1_1_7/ (unchanged).

Usage: python build_market_data_fixtures.py        # (re)writes fixtures/market_data/<case>/...
Cases (each holds one date dir 2026-10-20 unless noted; every pack is schema_version 1.1.8 unless noted):
  valid                 the example pack: 4 tickers, OK / STALE / UNKNOWN / UNCONFIRMED fields, as_of_basis on every
                        valued price / chain field, origin-tagged earnings sources, delayed chain fields (the 1.1.7
                        example wrongly said delayed false), a robots_only_no_terms source, sources_not_used,
                        catalysts.json (catalysts + ticker_context), summary.md
  stale                 JPM last_price marked OK at 20m01s before pack_built_at          -> MD-05-STALE, exit 1
  missing_value         JPM last_price value null but status OK                                  -> MD-06-UNKNOWN, exit 1
  malformed_json        WDC.json truncated (its manifest hash matches the broken bytes)  -> MD-03-JSON, exit 2
  tampered_hash         JPM.json edited after pack.sha256 was written                    -> MD-02-HASH, exit 1
  path_traversal        pack.sha256 lists ../outside.json (outside.json sits next to the date dir) -> MD-02-PATH, exit 1
  missing_manifest      no pack.sha256, plus a script and a stray .md (the shape of a non-conformant pack) -> MD-02-MISSING
  missing_basis         JPM last_price has a value but no as_of_basis                    -> MD-13-MISSING, exit 1
  undeclared_delay      a JPM chain field from the declared-delayed example_chain says delayed false (the 1.1.7
                        example's bug)                                                   -> MD-14-DELAY, exit 1
  earnings_same_origin  JPM earnings_date OK from the issuer's IR page and the same release re-published on another
                        domain (both origin issuer / JPM: one source)                   -> MD-07-UNCONFIRMED, exit 1
  catalysts_bad         catalysts.json: a CONFIRMED catalyst with one origin, an undated CONFIRMED one and a naive
                        event_time                     -> MD-15-UNCONFIRMED, MD-15-UNKNOWN, MD-15-TIME-NAIVE, exit 1
  catalysts_in_1_1_7    the 1.1.7 example pack (build_market_data_fixtures_v1_1_7) plus a listed catalysts.json:
                        1.1.7 packs may not hold it                                      -> MD-01-NAME, exit 1
Symlinks, executable bits, FIFOs, oversized files and absolute manifest paths are made at test time in tmp_path
(never vendored: the spec manifest and Poodle's sync must only carry regular files)."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import build_market_data_fixtures_v1_1_7 as b117

SPEC = Path(__file__).resolve().parent
OUT = SPEC / "fixtures" / "market_data"
DATE = "2026-10-20"
BUILT = "2026-10-20T09:00:00-04:00"
PACK_ID = "EXAMPLE-2026-10-20"
VERSION = "1.1.8"
COMMON = {"schema_version": VERSION, "kind": "ticker_snapshot", "pack_id": PACK_ID, "pack_built_at": BUILT,
          "data_label": "EXAMPLE"}
QUOTE_AS_OF = "2026-10-20T08:50:00-04:00"     # 10 min before the build: fresh
CHAIN_AS_OF = "2026-10-19T16:00:00-04:00"     # prior session close: older than 20 min -> STALE
NEWS_AS_OF = "2026-10-20T08:30:00-04:00"
PRIOR_CLOSE_AS_OF = "2026-10-19T16:00:00-04:00"
QUOTE_DELAY = CHAIN_DELAY = 15                # declared per source in index.json sources_terms (MD-14)


def F(value, source, as_of, status="OK", delayed=False, delay_minutes=None, as_of_basis=None, **kw):
    f = {"value": value, "source": source, "as_of": as_of}
    if as_of_basis is not None:
        f["as_of_basis"] = as_of_basis
    f["delayed"] = delayed
    if delay_minutes is not None:
        f["delay_minutes"] = delay_minutes
    f["status"] = status
    f.update(kw)
    return f


def unknown(note=None):
    f = {"value": None, "source": None, "as_of": None, "delayed": False, "status": "UNKNOWN"}
    if note:
        f["note"] = note
    return f


def quote(v):
    return F(v, "example_quotes", QUOTE_AS_OF, as_of_basis="quote_time", delayed=True, delay_minutes=QUOTE_DELAY,
             url="https://quotes.example.com/q")


def chain(v):
    """prior-session chain data from the delayed example_chain: delayed true (the 1.1.7 example said false), as_of =
    the last trade's time (the source shows no quote time: last_trade is the conservative fallback), STALE"""
    return F(v, "example_chain", CHAIN_AS_OF, status="STALE", as_of_basis="last_trade", delayed=True,
             delay_minutes=CHAIN_DELAY, url="https://chain.example.com/c")


def src(source, url, origin, entity, as_of=NEWS_AS_OF):
    return {"source": source, "url": url, "as_of": as_of, "origin": origin, "origin_entity": entity}


def ir(tk):
    return src("example_ir", "https://ir.example.com/events", "issuer", tk)


CALENDAR = src("example_calendar", "https://calendar.example.org/earnings", "data_vendor", "Example Calendar Co")
WIRE = src("example_wire", "https://www.example.org/wire", "news", "Example Wire")


def earnings_ok(value, tk):
    return F(value, "example_ir", NEWS_AS_OF, as_of_basis="published", sources=[ir(tk), CALENDAR])


def earnings_one(value):
    return F(value, "example_wire", NEWS_AS_OF, status="UNCONFIRMED", as_of_basis="published", sources=[WIRE],
             note="EXAMPLE: one source only")


def contract(cid, right, strike, oi, vol, bid, ask):
    return {"contract_id": cid, "right": right, "strike": strike, "expiry": "2026-10-23",
            "open_interest": chain(oi), "volume": chain(vol), "bid": chain(bid), "ask": chain(ask),
            "spread": chain(round(ask - bid, 2)), "spread_pct_of_mid": chain(round(100 * (ask - bid) / ((ask + bid) / 2), 2))}


def ticker(tk, watchlist, price, liquidity, iv, implied, edate, etime, **extra):
    d = dict(COMMON, ticker=tk, watchlist=watchlist)
    d.update(extra)
    d.update(last_price=quote(price), option_liquidity=liquidity, iv=iv, iv_rank=unknown("EXAMPLE: no free IV-rank source"),
             implied_move=implied, earnings_date=edate, earnings_time=etime)
    return d


SOURCES_TERMS = [
    {"source": "example_quotes", "domain": "example.com", "basis": "robots_and_terms_allow", "automated": True,
     "terms_url": "https://quotes.example.com/terms", "delayed": True, "delay_minutes": QUOTE_DELAY, "note": "EXAMPLE source"},
    {"source": "example_chain", "domain": "example.com", "basis": "robots_and_terms_allow", "automated": True,
     "delayed": True, "delay_minutes": CHAIN_DELAY},
    {"source": "example_ir", "domain": "example.com", "basis": "issuer_public_release", "automated": False},
    {"source": "example_calendar", "domain": "example.org", "basis": "robots_and_terms_allow", "automated": False,
     "terms_url": "https://calendar.example.org/terms"},
    {"source": "example_wire", "domain": "example.org", "basis": "manual_read_once", "automated": False},
    {"source": "example_screener", "domain": "example.org", "basis": "robots_only_no_terms", "automated": False,
     "note": "EXAMPLE: no terms page; robots.txt permits the page"}]
SOURCES_NOT_USED = [
    {"name": "example_portal", "domain": "example.com", "reason": "login_required"},
    {"name": "example_feed", "domain": "example.org", "reason": "contact_email_required",
     "note": "EXAMPLE: the free feed asks for a contact address before use; not used"}]


def catalysts_doc() -> dict:
    """catalysts.json of the example pack (fictional)"""
    return {"schema_version": VERSION, "kind": "catalyst_log", "pack_id": PACK_ID, "pack_built_at": BUILT,
            "data_label": "EXAMPLE", "date": DATE,
            "catalysts": [
                {"ticker": "JPM", "catalyst_type": "earnings", "event_date": "2026-10-21", "status": "CONFIRMED",
                 "sources": [ir("JPM"), CALENDAR], "note": "EXAMPLE: BMO per both sources"},
                {"ticker": "WDC", "catalyst_type": "earnings", "event_date": "2026-10-28", "status": "UNCONFIRMED",
                 "sources": [WIRE]},
                {"ticker": None, "catalyst_type": "fomc", "event_date": "2026-10-28",
                 "event_time": "2026-10-28T14:00:00-04:00", "status": "UNCONFIRMED", "sources": [CALENDAR],
                 "note": "EXAMPLE: macro, one source"},
                {"ticker": "XMPLA", "catalyst_type": "other", "event_date": None, "status": "UNKNOWN", "sources": [],
                 "note": "EXAMPLE: no catalyst source"}],
            "ticker_context": [
                {"ticker": "JPM", "sector": "Financials",
                 "prior_close": F(100.5, "example_quotes", PRIOR_CLOSE_AS_OF, as_of_basis="last_trade", delayed=True,
                                  delay_minutes=QUOTE_DELAY, url="https://quotes.example.com/q")},
                {"ticker": "XMPLB", "sector": "Technology", "analyst_actions": [
                    {"firm": "Example Securities", "action": "initiate", "from": None, "to": "Buy", "date": "2026-10-19",
                     "source": src("example_screener", "https://screener.example.org/x", "broker", "Example Securities")}]}]}


def valid_docs() -> dict[str, dict]:
    """the example pack (fictional values)"""
    t = {
        "JPM": ticker("JPM", "fixture", 101.25,
                      {"contracts": [contract("JPM 261023C102", "call", 102, 1200, 340, 1.10, 1.20),
                                     contract("JPM 261023P100", "put", 100, 900, 210, 0.95, 1.05)]},
                      chain(31.5), chain(3.2), earnings_ok("2026-10-21", "JPM"), earnings_ok("BMO", "JPM")),
        "WDC": ticker("WDC", "fixture", 55.5,
                      {"summary": {"expiry": "2026-10-23", "open_interest": chain(4300), "volume": chain(800),
                                   "spread_pct_of_mid": chain(6.5)}},
                      chain(48.0), chain(6.1), earnings_one("2026-10-28"),
                      F("unknown", None, None, status="UNKNOWN")),
        "XMPLA": ticker("XMPLA", "eugene", 20.0,
                        {"summary": {"open_interest": chain(150), "volume": chain(12), "spread_pct_of_mid": chain(14.0)}},
                        unknown(), unknown(), unknown(), unknown()),
        "XMPLB": ticker("XMPLB", "street", 12.34,
                        {"contracts": [contract("XMPLB 261023C12.5", "call", 12.5, 50, 5, 0.40, 0.55)]},
                        chain(72.0), chain(9.0), unknown(), unknown(), candidate_status="idea"),
    }
    index = {"schema_version": VERSION, "kind": "pack_index", "pack_id": PACK_ID, "pack_built_at": BUILT,
             "data_label": "EXAMPLE", "date": DATE,
             "tickers": [{"ticker": k, "file": f"{k}.json", "watchlist": v["watchlist"],
                          **({"candidate_status": v["candidate_status"]} if "candidate_status" in v else {})}
                         for k, v in t.items()],
             "sources_terms": copy.deepcopy(SOURCES_TERMS),
             "sources_not_used": copy.deepcopy(SOURCES_NOT_USED)}
    # deep copy: CALENDAR / WIRE are shared module-level dicts, and callers mutate the result
    return copy.deepcopy({"index.json": index, "catalysts.json": catalysts_doc(), **{f"{k}.json": v for k, v in t.items()}})


SUMMARY = ("# EXAMPLE market-data pack 2026-10-20 (fixture, not market data, spec 1.1.8)\n\n"
           "All values are fictional EXAMPLE data for spec tests. JPM and WDC are fixture tickers; XMPLA / XMPLB are\n"
           "made-up symbols. XMPLB is a street idea only (candidate_status idea).\n"
           "A BMO / AMC session inferred from a conference-call time would be noted here only, never as earnings_time.\n").encode()


def dump(doc) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()


def manifest(files: dict[str, bytes]) -> bytes:
    return "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in sorted(files.items())).encode()


def pack(docs: dict[str, dict], summary: bool = True) -> dict[str, bytes]:
    files = {n: dump(d) for n, d in docs.items()}
    if summary:
        files["summary.md"] = SUMMARY
    return files


def with_manifest(files: dict[str, bytes]) -> dict[str, bytes]:
    return {**files, "pack.sha256": manifest(files)}


def build() -> dict[str, dict[str, bytes]]:
    """case -> {path relative to the case dir: bytes}"""
    cases: dict[str, dict[str, bytes]] = {}
    d = lambda files: {f"{DATE}/{n}": b for n, b in files.items()}   # noqa: E731
    cases["valid"] = d(with_manifest(pack(valid_docs())))

    docs = valid_docs()
    docs["JPM.json"]["last_price"]["as_of"] = "2026-10-20T08:39:59-04:00"   # 20m01s before 09:00:00
    cases["stale"] = d(with_manifest(pack(docs)))

    docs = valid_docs()
    docs["JPM.json"]["last_price"]["value"] = None                          # status stays OK
    cases["missing_value"] = d(with_manifest(pack(docs)))

    files = pack(valid_docs())
    files["WDC.json"] = files["WDC.json"][: len(files["WDC.json"]) // 2]     # truncated, hashed as is
    cases["malformed_json"] = d(with_manifest(files))

    files = with_manifest(pack(valid_docs()))
    docs = valid_docs()
    docs["JPM.json"]["last_price"]["value"] = 111.25                        # edited after hashing
    files["JPM.json"] = dump(docs["JPM.json"])
    cases["tampered_hash"] = d(files)

    files = with_manifest(pack(valid_docs()))
    outside = dump(dict(valid_docs()["JPM.json"], ticker="OUTSIDE"))
    files["pack.sha256"] += f"{hashlib.sha256(outside).hexdigest()}  ../outside.json\n".encode()
    cases["path_traversal"] = {**d(files), "outside.json": outside}

    files = pack(valid_docs())
    files["refresh_quotes.py"] = b"# EXAMPLE stray script: a pack dir must never hold code\nraise SystemExit(1)\n"
    files["notes.md"] = b"# EXAMPLE stray note\n"
    cases["missing_manifest"] = d(files)

    docs = valid_docs()
    del docs["JPM.json"]["last_price"]["as_of_basis"]
    cases["missing_basis"] = d(with_manifest(pack(docs)))

    docs = valid_docs()
    bid = docs["JPM.json"]["option_liquidity"]["contracts"][0]["bid"]
    bid["delayed"] = False                                                  # the 1.1.7 example's bug
    del bid["delay_minutes"]
    cases["undeclared_delay"] = d(with_manifest(pack(docs)))

    docs = valid_docs()
    docs["JPM.json"]["earnings_date"]["sources"][1] = src(
        "example_wire", "https://www.example.org/wire/jpm-release", "issuer", "JPM")   # the same release, re-published
    cases["earnings_same_origin"] = d(with_manifest(pack(docs)))

    docs = valid_docs()
    cat = docs["catalysts.json"]["catalysts"]
    cat[0]["sources"] = [ir("JPM"), src("example_wire", "https://www.example.org/wire/jpm-release", "issuer", "JPM")]
    cat.append({"ticker": "WDC", "catalyst_type": "investor_day", "event_date": None, "status": "CONFIRMED",
                "sources": [WIRE, CALENDAR]})
    cat[2]["event_time"] = "2026-10-28T14:00:00"
    cases["catalysts_bad"] = d(with_manifest(pack(docs)))

    files = b117.pack(b117.valid_docs())
    cat = catalysts_doc()
    cat["schema_version"] = "1.1.7"
    files["catalysts.json"] = dump(cat)
    cases["catalysts_in_1_1_7"] = d(with_manifest(files))
    return cases


EXPECTED = {   # case -> (exit code, ids that must appear)
    "valid": (0, set()),
    "stale": (1, {"MD-05-STALE"}),
    "missing_value": (1, {"MD-06-UNKNOWN"}),
    "malformed_json": (2, {"MD-03-JSON"}),
    "tampered_hash": (1, {"MD-02-HASH"}),
    "path_traversal": (1, {"MD-02-PATH"}),
    "missing_manifest": (1, {"MD-02-MISSING"}),
    "missing_basis": (1, {"MD-13-MISSING"}),
    "undeclared_delay": (1, {"MD-14-DELAY"}),
    "earnings_same_origin": (1, {"MD-07-UNCONFIRMED"}),
    "catalysts_bad": (1, {"MD-15-UNCONFIRMED", "MD-15-UNKNOWN", "MD-15-TIME-NAIVE"}),
    "catalysts_in_1_1_7": (1, {"MD-01-NAME"}),
}


def write(root: Path = OUT) -> None:
    if root.exists():
        shutil.rmtree(root)
    for case, files in build().items():
        for rel, b in files.items():
            p = root / case / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b)
            p.chmod(0o644)


if __name__ == "__main__":
    write()
    print(f"wrote {sum(len(f) for f in build().values())} files under {OUT.relative_to(SPEC)}")
