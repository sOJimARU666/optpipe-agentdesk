"""Validate Oodle's market_snapshot pack files (spec 1.1.8): JSON Schema market_snapshot.schema.json (MD-03) plus the
rules MD-01..MD-15 of market_data_rules.yaml (YAML wins over market_data_rules.md). PAPER ONLY, read-only, no network.
Documents with schema_version 1.1.8 get the 1.1.8 rules (*V118 / CatalystLog defs, MD-13..MD-15, origin-based MD-07);
every other document takes the unchanged 1.1.7 path (same checks, same error strings as spec 1.1.7).

Usage:
  python validate_market_snapshot.py [PATH ...]
    PATH = a date dir (<root>/<YYYY-MM-DD>/: full pack check through market_data_loader.load_pack: layout, pack.sha256
           manifest, every file, cross-file consistency) or a single pack JSON file (document checks only).
    No PATH = the example pack fixtures/market_data/valid/2026-10-20.

Exit codes (same fail-closed contract as validate_session.py):
  0  every path was read and validated with no errors
  1  at least one pack / document has rule errors (MD-01..MD-15)
  2  at least one path is missing / unreadable, a file is not UTF-8 JSON or not a JSON object (MD-01-DIR,
     MD-01-UNREADABLE, MD-03-JSON), or the validator / loader raised (MD-00-CRASH)
Consumers must treat ANY non-zero exit, and a missing or unimportable validator, as FAIL.

Every error string starts with its id token, e.g. `MD-05-STALE JPM.json last_price: ...`.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from jsonschema import Draft202012Validator

SPEC = Path(__file__).resolve().parent
NY = ZoneInfo("America/New_York")
SCHEMA = json.loads((SPEC / "market_snapshot.schema.json").read_text(encoding="utf-8"))
EXAMPLE_PACK = SPEC / "fixtures" / "market_data" / "valid" / "2026-10-20"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
FATAL_IDS = ("MD-01-DIR", "MD-01-UNREADABLE", "MD-03-JSON", "MD-00-CRASH")   # exit 2 classes


def load_rules() -> dict:
    return yaml.safe_load((SPEC / "market_data_rules.yaml").read_text(encoding="utf-8"))


RULES = load_rules()
STALENESS_LIMIT = timedelta(minutes=RULES["staleness"]["staleness_limit_minutes"])
QUOTE_FIELDS = frozenset(RULES["staleness"]["quote_fields"])
EARNINGS_FIELDS = frozenset(RULES["earnings"]["fields"])
MIN_EARNINGS_SOURCES = RULES["earnings"]["min_independent_sources"]
TOP_FIELDS = ("last_price", "iv", "iv_rank", "implied_move", "earnings_date", "earnings_time")
CONTRACT_FIELDS = ("open_interest", "volume", "bid", "ask", "spread", "spread_pct_of_mid")
SUMMARY_FIELDS = ("open_interest", "volume", "spread_pct_of_mid")
# ---- spec 1.1.8 (schema_version 1.1.8 documents only)
V118 = "1.1.8"
ORIGINS = frozenset(RULES["earnings"]["origins"])
ISSUER_CHANNEL_DOMAINS = frozenset(RULES["earnings"]["issuer_channel_domains"])
ISSUER_HOST_LABELS = frozenset(RULES["earnings"]["issuer_host_labels"])
AS_OF_BASIS_REQUIRED = frozenset(RULES["as_of_basis"]["required_for"])
CATALYSTS = RULES["layout"]["catalysts"]
DEFS_V117 = {"ticker_snapshot": "TickerSnapshot", "pack_index": "PackIndex"}
DEFS_V118 = {"ticker_snapshot": "TickerSnapshotV118", "pack_index": "PackIndexV118", "catalyst_log": "CatalystLog"}


def _validator(kind: str | None, defs: dict = DEFS_V117) -> Draft202012Validator:
    name = defs.get(kind)
    sub = ({"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]} if name else
           {"oneOf": [{"$ref": f"#/$defs/{n}"} for n in defs.values()], "$defs": SCHEMA["$defs"]})
    return Draft202012Validator(sub)


_VALIDATORS = {k: _validator(k) for k in ("ticker_snapshot", "pack_index", None)}   # 1.1.7 documents (unchanged)
_VALIDATORS_V118 = {k: _validator(k, DEFS_V118) for k in ("ticker_snapshot", "pack_index", "catalyst_log", None)}


def exit_code(errors: list[str]) -> int:
    """0 no errors; 2 if any error is of an unreadable / crash class; else 1"""
    if not errors:
        return 0
    return 2 if any(e.split(" ", 1)[0] in FATAL_IDS for e in errors) else 1


def parse_time(s, inv: str, where: str, errors: list[str]):
    """an aware datetime, or None with an MD-04 (or `inv`) -TIME-NAIVE / -TIME-INVALID error appended. Never raises."""
    if not isinstance(s, str):
        errors.append(f"{inv}-TIME-INVALID {where}: not a string ({type(s).__name__})")
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        errors.append(f"{inv}-TIME-INVALID {where}: {s!r} is not ISO 8601")
        return None
    if t.tzinfo is None or t.utcoffset() is None:
        errors.append(f"{inv}-TIME-NAIVE {where}: {s!r} has no UTC offset")
        return None
    return t


def registrable_domain(url: str) -> str | None:
    """last two labels of the URL host, lower-case, leading www. dropped (see market_data_rules.yaml earnings)"""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    labels = [x for x in host.split(".") if x]
    return ".".join(labels[-2:]) if len(labels) >= 2 else None


def _strings(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)
    elif isinstance(x, str):
        yield x


def email_errors(doc, name: str) -> list[str]:
    """MD-09-EMAIL: no e-mail address anywhere (keys and values), placeholder domains included"""
    found = sorted({m for s in _strings(doc) for m in EMAIL.findall(s)})
    return [f"MD-09-EMAIL {name}: e-mail address found (pack files hold tickers and market data only)"] if found else []


def iter_fields(doc: dict):
    """(path, leaf_name, field_dict) for every sourced field of a ticker_snapshot (defensive: skips non-objects)"""
    for k in TOP_FIELDS:
        if isinstance(doc.get(k), dict):
            yield k, k, doc[k]
    ol = doc.get("option_liquidity")
    if isinstance(ol, dict):
        for i, c in enumerate(ol.get("contracts") or []):
            if isinstance(c, dict):
                for k in CONTRACT_FIELDS:
                    if isinstance(c.get(k), dict):
                        yield f"option_liquidity.contracts[{i}].{k}", k, c[k]
        s = ol.get("summary")
        if isinstance(s, dict):
            for k in SUMMARY_FIELDS:
                if isinstance(s.get(k), dict):
                    yield f"option_liquidity.summary.{k}", k, s[k]


def field_errors(path: str, leaf: str, f: dict, built, name: str, v118: bool = False, ticker=None) -> list[str]:
    """MD-04..MD-08 for one field. `built` = aware pack_built_at or None (then time comparisons are skipped: the
    header already carries an MD-04 error, so the document fails anyway). v118 = a 1.1.8 document: MD-07 by origin
    (not domain), MD-07-ORIGIN, MD-13, MD-04 on sources[].as_of; `ticker` = the document's ticker (MD-07-ORIGIN)."""
    e: list[str] = []
    w = f"{name} {path}"
    status, value = f.get("status"), f.get("value")
    no_value = value is None or (leaf == "earnings_time" and value == "unknown")
    if no_value and status != "UNKNOWN":
        e.append(f"MD-06-UNKNOWN {w}: value is {'missing' if 'value' not in f else repr(value)} but status is {status}; it must be UNKNOWN")
    if status == "UNKNOWN" and not no_value:
        e.append(f"MD-06-VALUE {w}: status UNKNOWN carries a value ({value!r}); missing data stays missing")
    if not no_value and (f.get("source") is None or f.get("as_of") is None):
        e.append(f"MD-06-UNSOURCED {w}: a value needs a source and an as_of (unsourced data stays UNKNOWN)")
    t = None
    if f.get("as_of") is not None:
        t = parse_time(f["as_of"], "MD-04", f"{w}.as_of", e)
    if t is not None and built is not None:
        if t > built:
            e.append(f"MD-04-FUTURE {w}: as_of {f['as_of']} is after pack_built_at")
        elif leaf in QUOTE_FIELDS and status == "OK" and built - t > STALENESS_LIMIT:
            e.append(f"MD-05-STALE {w}: status OK but as_of {f['as_of']} is {built - t} before pack_built_at "
                     f"(limit {STALENESS_LIMIT}); it must be STALE")
    delayed, dm = f.get("delayed"), f.get("delay_minutes")
    if delayed is True and not (isinstance(dm, int) and not isinstance(dm, bool) and dm >= 1):
        e.append(f"MD-08-DELAY {w}: delayed true needs delay_minutes >= 1")
    if delayed is False and dm is not None:
        e.append(f"MD-08-DELAY {w}: delayed false carries delay_minutes ({dm!r})")
    if v118:
        e += _v118_field_errors(w, leaf, f, no_value, built, ticker)
        if leaf in EARNINGS_FIELDS:
            if status == "OK":
                e += _origin_count_errors("MD-07-UNCONFIRMED", w, f.get("sources"), "status OK", "it must be UNCONFIRMED")
        elif status == "UNCONFIRMED":
            e.append(f"MD-07-STATUS {w}: UNCONFIRMED is only for earnings fields (use OK, STALE or UNKNOWN)")
        return e
    if leaf in EARNINGS_FIELDS:
        if status == "OK":
            doms = {registrable_domain(s.get("url", "")) for s in f.get("sources") or [] if isinstance(s, dict)}
            doms.discard(None)
            if len(doms) < MIN_EARNINGS_SOURCES:
                e.append(f"MD-07-UNCONFIRMED {w}: status OK needs >= {MIN_EARNINGS_SOURCES} sources with distinct "
                         f"domains (found {sorted(doms)}); it must be UNCONFIRMED")
    elif status == "UNCONFIRMED":
        e.append(f"MD-07-STATUS {w}: UNCONFIRMED is only for earnings fields (use OK, STALE or UNKNOWN)")
    return e


def _origin_pairs(sources) -> set:
    """distinct (origin, casefolded origin_entity) pairs of a 1.1.8 sources list (malformed entries ignored: MD-03)"""
    return {(s.get("origin"), s.get("origin_entity").casefold()) for s in sources or []
            if isinstance(s, dict) and s.get("origin") in ORIGINS and isinstance(s.get("origin_entity"), str)}


def _origin_count_errors(inv: str, w: str, sources, what: str, must: str) -> list[str]:
    pairs = _origin_pairs(sources)
    if len(pairs) >= MIN_EARNINGS_SOURCES:
        return []
    return [f"{inv} {w}: {what} needs >= {MIN_EARNINGS_SOURCES} sources with distinct (origin, origin_entity) "
            f"(found {sorted(pairs)}); {must}"]


def _issuer_channel(url) -> bool:
    """True if the URL is an issuer channel: a newswire / IR-hosting domain, or an IR host label (ir., investor., ...)"""
    if not isinstance(url, str):
        return False
    if registrable_domain(url) in ISSUER_CHANNEL_DOMAINS:
        return True
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host.split(".", 1)[0] in ISSUER_HOST_LABELS


def source_ref_errors(w: str, s, built, ticker) -> list[str]:
    """1.1.8 checks of one SourceRefV118: MD-07-ORIGIN (issuer channel / issuer entity), MD-04 on its as_of"""
    e: list[str] = []
    if not isinstance(s, dict):
        return e
    origin, ent = s.get("origin"), s.get("origin_entity")
    if _issuer_channel(s.get("url")) and origin != "issuer":
        e.append(f"MD-07-ORIGIN {w}: {s.get('url')!r} is an issuer channel (IR site or newswire); its origin must be "
                 f"issuer, not {origin!r} (an issuer release counts once wherever it is read)")
    if origin == "issuer" and ticker is not None and isinstance(ent, str) and ent.casefold() != ticker.casefold():
        e.append(f"MD-07-ORIGIN {w}: origin issuer has origin_entity {ent!r}; for an issuer it must be the ticker {ticker}")
    if s.get("as_of") is not None:
        t = parse_time(s["as_of"], "MD-04", f"{w}.as_of", e)
        if t is not None and built is not None and t > built:
            e.append(f"MD-04-FUTURE {w}: as_of {s['as_of']} is after pack_built_at")
    return e


def _v118_field_errors(w: str, leaf: str, f: dict, no_value: bool, built, ticker) -> list[str]:
    """MD-13 and the per-source 1.1.8 checks for one field of a 1.1.8 document"""
    e: list[str] = []
    basis = f.get("as_of_basis")
    if leaf in AS_OF_BASIS_REQUIRED and not no_value and basis is None:
        e.append(f"MD-13-MISSING {w}: a 1.1.8 {leaf} with a value needs as_of_basis "
                 f"(quote_time, last_trade as a conservative fallback, fetch_minus_delay or published)")
    if basis == "fetch_minus_delay" and f.get("delayed") is not True:
        e.append(f"MD-13-DELAY {w}: as_of_basis fetch_minus_delay needs delayed true with delay_minutes")
    for i, s in enumerate(f.get("sources") or []):
        e += source_ref_errors(f"{w}.sources[{i}]", s, built, ticker)
    return e


def _pack_date(built):
    return built.astimezone(NY).date() if built is not None else None


def catalyst_errors(doc: dict, built, name: str) -> list[str]:
    """MD-15 (and MD-04 / MD-06 / MD-07-ORIGIN / MD-13 on nested fields) for a 1.1.8 catalyst_log"""
    e: list[str] = []
    for i, c in enumerate(doc.get("catalysts") or []):
        if not isinstance(c, dict):
            continue
        w = f"{name} catalysts[{i}]"
        tk = c.get("ticker") if isinstance(c.get("ticker"), str) else None
        status, edate = c.get("status"), c.get("event_date")
        if edate is None and status != "UNKNOWN":
            e.append(f"MD-15-UNKNOWN {w}: event_date is null but status is {status}; an undated catalyst is UNKNOWN")
        if status == "UNKNOWN" and edate is not None:
            e.append(f"MD-15-VALUE {w}: status UNKNOWN carries event_date {edate!r}; use UNCONFIRMED or CONFIRMED")
        d = None
        if isinstance(edate, str):
            try:
                d = date.fromisoformat(edate)
            except ValueError:
                e.append(f"MD-15-DATE {w}: event_date {edate!r} is not a calendar date")
            if not c.get("sources"):
                e.append(f"MD-15-UNSOURCED {w}: a dated catalyst needs at least one source (unsourced stays UNKNOWN)")
        if status == "CONFIRMED":
            e += _origin_count_errors("MD-15-UNCONFIRMED", w, c.get("sources"), "status CONFIRMED", "it must be UNCONFIRMED")
        if c.get("event_time") is not None:
            t = parse_time(c["event_time"], "MD-15", f"{w}.event_time", e)
            if t is not None:
                if edate is None:
                    e.append(f"MD-15-DATE {w}: event_time {c['event_time']} without an event_date")
                elif d is not None and t.astimezone(NY).date() != d:
                    e.append(f"MD-15-DATE {w}: event_time {c['event_time']} is {t.astimezone(NY).date().isoformat()} "
                             f"in America/New_York, not event_date {edate}")
        for j, s in enumerate(c.get("sources") or []):
            e += source_ref_errors(f"{w}.sources[{j}]", s, built, tk)
    seen = set()
    for i, x in enumerate(doc.get("ticker_context") or []):
        if not isinstance(x, dict):
            continue
        tk = x.get("ticker")
        w = f"ticker_context[{i}]"
        if tk in seen:
            e.append(f"MD-15-DUPLICATE {name} {w}: ticker {tk} listed twice (one context entry per ticker)")
        seen.add(tk)
        tk = tk if isinstance(tk, str) else None
        if isinstance(x.get("prior_close"), dict):
            e += field_errors(f"{w}.prior_close", "prior_close", x["prior_close"], built, name, v118=True, ticker=tk)
        for j, a in enumerate(x.get("analyst_actions") or []):
            if not isinstance(a, dict):
                continue
            aw = f"{name} {w}.analyst_actions[{j}]"
            pd = _pack_date(built)
            try:
                ad = date.fromisoformat(a.get("date")) if isinstance(a.get("date"), str) else None
            except ValueError:
                ad = None
                e.append(f"MD-15-DATE {aw}: date {a.get('date')!r} is not a calendar date")
            if ad is not None and pd is not None and ad > pd:
                e.append(f"MD-04-FUTURE {aw}: date {a['date']} is after the pack date {pd.isoformat()}")
            e += source_ref_errors(f"{aw}.source", a.get("source"), built, tk)
    return e


def _validate_v118(doc: dict, name: str) -> list[str]:
    kind = doc.get("kind") if doc.get("kind") in DEFS_V118 else None
    e = [f"MD-03-SCHEMA {name} {'/'.join(map(str, err.absolute_path)) or '(root)'}: {err.message}"
         for err in sorted(_VALIDATORS_V118[kind].iter_errors(doc), key=lambda x: (list(map(str, x.absolute_path)), x.message))]
    built = None
    if "pack_built_at" in doc:
        built = parse_time(doc["pack_built_at"], "MD-04", f"{name} pack_built_at", e)
    if kind == "ticker_snapshot":
        tk = doc.get("ticker") if isinstance(doc.get("ticker"), str) else None
        for path, leaf, f in iter_fields(doc):
            e += field_errors(path, leaf, f, built, name, v118=True, ticker=tk)
        if doc.get("watchlist") == "street" and doc.get("candidate_status", "idea") != "idea":
            e.append(f"MD-10-STREET-CANDIDATE {name}: street ticker has candidate_status "
                     f"{doc.get('candidate_status')!r}; street tickers are ideas only until R01-R13 pass in a session record")
    elif kind == "pack_index":
        for i, t in enumerate(doc.get("tickers") or []):
            if isinstance(t, dict) and t.get("watchlist") == "street" and t.get("candidate_status", "idea") != "idea":
                e.append(f"MD-10-STREET-CANDIDATE {name} tickers[{i}]: street ticker {t.get('ticker')} has candidate_status "
                         f"{t.get('candidate_status')!r}; it must be idea")
        used = {s.get("source") for s in doc.get("sources_terms") or [] if isinstance(s, dict)}
        for i, s in enumerate(doc.get("sources_not_used") or []):
            if isinstance(s, dict) and s.get("name") in used:
                e.append(f"MD-12-CONFLICT {name} sources_not_used[{i}]: {s.get('name')!r} is also a used source in sources_terms")
    elif kind == "catalyst_log":
        e += catalyst_errors(doc, built, name)
    e += email_errors(doc, name)
    return e


def validate_document(doc, name: str = "<doc>") -> list[str]:
    """document-level checks of one pack JSON (ticker_snapshot or pack_index): MD-03 schema, MD-04..MD-10.
    Cross-file rules (MD-01-DATE, MD-02, MD-11, MD-12, MD-14) need the whole pack: market_data_loader.load_pack.
    schema_version 1.1.8 documents (also catalyst_log) get the 1.1.8 rules; all others the unchanged 1.1.7 path."""
    if not isinstance(doc, dict):
        return [f"MD-03-JSON {name}: a JSON {type(doc).__name__}, not an object"]
    if doc.get("schema_version") == V118:
        return _validate_v118(doc, name)
    kind = doc.get("kind") if doc.get("kind") in ("ticker_snapshot", "pack_index") else None
    e = [f"MD-03-SCHEMA {name} {'/'.join(map(str, err.absolute_path)) or '(root)'}: {err.message}"
         for err in sorted(_VALIDATORS[kind].iter_errors(doc), key=lambda x: (list(map(str, x.absolute_path)), x.message))]
    built = None
    if "pack_built_at" in doc:
        built = parse_time(doc["pack_built_at"], "MD-04", f"{name} pack_built_at", e)
    if kind == "ticker_snapshot":
        for path, leaf, f in iter_fields(doc):
            e += field_errors(path, leaf, f, built, name)
        if doc.get("watchlist") == "street" and doc.get("candidate_status", "idea") != "idea":
            e.append(f"MD-10-STREET-CANDIDATE {name}: street ticker has candidate_status "
                     f"{doc.get('candidate_status')!r}; street tickers are ideas only until R01-R13 pass in a session record")
    elif kind == "pack_index":
        for i, t in enumerate(doc.get("tickers") or []):
            if isinstance(t, dict) and t.get("watchlist") == "street" and t.get("candidate_status", "idea") != "idea":
                e.append(f"MD-10-STREET-CANDIDATE {name} tickers[{i}]: street ticker {t.get('ticker')} has candidate_status "
                         f"{t.get('candidate_status')!r}; it must be idea")
    e += email_errors(doc, name)
    return e


def candidate_status(entry: dict) -> str:
    """the candidate_status with its default (absent = idea)"""
    return entry.get("candidate_status", RULES["candidate_status"]["default"])


def pack_errors(index: dict, snapshots: dict[str, dict], dir_name: str, catalysts: dict | None = None) -> list[str]:
    """cross-file rules for a parsed pack: MD-01-DATE, MD-11, MD-12 (+ MD-14 and catalysts.json in 1.1.8 packs).
    `snapshots` = {file name: parsed ticker doc}; `catalysts` = parsed catalysts.json (1.1.8 packs only) or None."""
    e = _pack_errors_v117(index, snapshots, dir_name)
    if index.get("schema_version") == V118:
        e += _pack_errors_v118(index, snapshots, catalysts)
    return e


def _catalyst_sources(doc: dict):
    """(source id, path, field-or-None) for every source used in a catalyst_log"""
    for i, c in enumerate(doc.get("catalysts") or []):
        if isinstance(c, dict):
            for j, s in enumerate(c.get("sources") or []):
                if isinstance(s, dict) and s.get("source"):
                    yield s["source"], f"catalysts[{i}].sources[{j}]", None
    for i, x in enumerate(doc.get("ticker_context") or []):
        if not isinstance(x, dict):
            continue
        pc = x.get("prior_close")
        if isinstance(pc, dict):
            if pc.get("source") is not None:
                yield pc["source"], f"ticker_context[{i}].prior_close", pc
        for j, a in enumerate(x.get("analyst_actions") or []):
            if isinstance(a, dict) and isinstance(a.get("source"), dict) and a["source"].get("source"):
                yield a["source"]["source"], f"ticker_context[{i}].analyst_actions[{j}].source", None


def _pack_errors_v118(index: dict, snapshots: dict[str, dict], catalysts: dict | None) -> list[str]:
    """1.1.8 cross-file rules: MD-11-PACK / MD-12-SOURCE for catalysts.json, MD-14-DELAY for every field"""
    e: list[str] = []
    fields = [(fname, path, f) for fname, doc in sorted(snapshots.items()) for path, _, f in iter_fields(doc)]
    if catalysts is not None:
        for k in ("schema_version", "pack_id", "pack_built_at", "data_label", "date"):
            if catalysts.get(k) != index.get(k):
                e.append(f"MD-11-PACK {CATALYSTS}: {k} {catalysts.get(k)!r} != index.json {index.get(k)!r}")
        fields += [(CATALYSTS, path, f) for _, path, f in _catalyst_sources(catalysts) if f is not None]
    terms = index.get("sources_terms")
    if terms is None:
        return e
    if catalysts is not None:
        listed = {s.get("source") for s in terms if isinstance(s, dict)}
        for src, path in sorted({(s, p) for s, p, _ in _catalyst_sources(catalysts)}):
            if src not in listed:
                e.append(f"MD-12-SOURCE {CATALYSTS} {path}: source {src!r} is not listed in index.json sources_terms")
    declared = {s["source"]: s.get("delay_minutes") for s in terms
                if isinstance(s, dict) and s.get("delayed") is True and isinstance(s.get("source"), str)}
    for fname, path, f in fields:
        src = f.get("source")
        if src not in declared or f.get("value") is None:
            continue
        need, dm = declared[src], f.get("delay_minutes")
        if f.get("delayed") is not True:
            e.append(f"MD-14-DELAY {fname} {path}: source {src!r} is declared delayed ({need} min) in index.json "
                     f"sources_terms, but the field says delayed {f.get('delayed')!r}")
        elif isinstance(need, int) and isinstance(dm, int) and not isinstance(dm, bool) and dm < need:
            e.append(f"MD-14-DELAY {fname} {path}: delay_minutes {dm} < the {need} min declared for source {src!r}")
    return e


def _pack_errors_v117(index: dict, snapshots: dict[str, dict], dir_name: str) -> list[str]:
    """the spec 1.1.7 cross-file rules, unchanged (MD-01-DATE, MD-11, MD-12)"""
    e: list[str] = []
    built = parse_time(index.get("pack_built_at"), "MD-04", "index.json pack_built_at", [])
    if not re.fullmatch(RULES["layout"]["date_dir_pattern"], dir_name):
        e.append(f"MD-01-DATE {dir_name}: date dir name is not YYYY-MM-DD")
    if index.get("date") != dir_name:
        e.append(f"MD-01-DATE index.json: date {index.get('date')!r} != date dir {dir_name!r}")
    if built is not None and built.astimezone(NY).date().isoformat() != dir_name:
        e.append(f"MD-01-DATE index.json: pack_built_at {index.get('pack_built_at')} is "
                 f"{built.astimezone(NY).date().isoformat()} in America/New_York, not {dir_name}")
    seen = set()
    for i, t in enumerate(index.get("tickers") or []):
        tk = t.get("ticker")
        if tk in seen:
            e.append(f"MD-11-DUPLICATE index.json tickers[{i}]: ticker {tk} listed twice (one file per ticker per day)")
        seen.add(tk)
        if t.get("file") != f"{tk}.json":
            e.append(f"MD-11-TICKER index.json tickers[{i}]: file {t.get('file')!r} != {tk}.json")
        doc = snapshots.get(t.get("file"))
        if doc is None:
            continue
        for k in ("schema_version", "pack_id", "pack_built_at", "data_label"):
            if doc.get(k) != index.get(k):
                e.append(f"MD-11-PACK {t.get('file')}: {k} {doc.get(k)!r} != index.json {index.get(k)!r}")
        for k in ("ticker", "watchlist"):
            if doc.get(k) != t.get(k):
                e.append(f"MD-11-TICKER {t.get('file')}: {k} {doc.get(k)!r} != index entry {t.get(k)!r}")
        if candidate_status(doc) != candidate_status(t):
            e.append(f"MD-11-TICKER {t.get('file')}: candidate_status {candidate_status(doc)!r} != index entry {candidate_status(t)!r}")
    terms = index.get("sources_terms")
    if terms is not None:
        listed = {s.get("source") for s in terms if isinstance(s, dict)}
        for fname, doc in sorted(snapshots.items()):
            used = set()
            for path, _, f in iter_fields(doc):
                if f.get("source") is not None:
                    used.add((f["source"], path))
                for s in f.get("sources") or []:
                    if isinstance(s, dict) and s.get("source"):
                        used.add((s["source"], path + ".sources"))
            for src, path in sorted(used):
                if src not in listed:
                    e.append(f"MD-12-SOURCE {fname} {path}: source {src!r} is not listed in index.json sources_terms")
    return e


def main(argv: list[str]) -> int:
    """CLI: 0 all valid; 1 any rule error; 2 any unreadable / unparsable path or a crash. Worst code wins."""
    import market_data_loader as mdl   # the loader imports this module; import late
    paths = [Path(a) for a in argv] or [EXAMPLE_PACK]
    rc = 0
    for p in paths:
        try:
            if p.is_dir() or p.is_symlink() or not p.suffix:
                res = mdl.load_pack(p)
                errs = res.errors
                sv = res.index.get("schema_version") if isinstance(res.index, dict) else None
            else:
                try:
                    doc = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, ValueError) as ex:
                    errs = [f"MD-03-JSON {p}: cannot read/parse: {type(ex).__name__}: {ex}"]
                else:
                    errs = validate_document(doc, p.name)
                    sv = doc.get("schema_version") if isinstance(doc, dict) else None
        except Exception as ex:  # noqa: BLE001 - a crashing validator must never look like a pass
            errs = [f"MD-00-CRASH {p}: validator raised {type(ex).__name__}: {ex}"]
        if errs:
            print("\n".join(f"{p}: {x}" for x in errs), file=sys.stderr if exit_code(errs) == 2 else sys.stdout)
        else:
            print(f"OK: {p} valid (market_snapshot {RULES['market_data_rules_version']} rules, schema_version {sv}, "
                  + ("pack rules MD-00..MD-15)" if p.is_dir() else "document rules MD-03..MD-10, MD-13, MD-15)"))
        rc = max(rc, exit_code(errs))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
