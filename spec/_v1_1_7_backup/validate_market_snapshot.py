"""Validate Oodle's market_snapshot pack files (spec 1.1.7): JSON Schema market_snapshot.schema.json (MD-03) plus the
rules MD-01..MD-12 of market_data_rules.yaml (YAML wins over market_data_rules.md). PAPER ONLY, read-only, no network.

Usage:
  python validate_market_snapshot.py [PATH ...]
    PATH = a date dir (<root>/<YYYY-MM-DD>/: full pack check through market_data_loader.load_pack: layout, pack.sha256
           manifest, every file, cross-file consistency) or a single pack JSON file (document checks only).
    No PATH = the example pack fixtures/market_data/valid/2026-10-20.

Exit codes (same fail-closed contract as validate_session.py):
  0  every path was read and validated with no errors
  1  at least one pack / document has rule errors (MD-01..MD-12)
  2  at least one path is missing / unreadable, a file is not UTF-8 JSON or not a JSON object (MD-01-DIR,
     MD-01-UNREADABLE, MD-03-JSON), or the validator / loader raised (MD-00-CRASH)
Consumers must treat ANY non-zero exit, and a missing or unimportable validator, as FAIL.

Every error string starts with its id token, e.g. `MD-05-STALE JPM.json last_price: ...`.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
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


def _validator(kind: str) -> Draft202012Validator:
    name = {"ticker_snapshot": "TickerSnapshot", "pack_index": "PackIndex"}.get(kind)
    sub = {"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]} if name else SCHEMA
    return Draft202012Validator(sub)


_VALIDATORS = {k: _validator(k) for k in ("ticker_snapshot", "pack_index", None)}


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


def field_errors(path: str, leaf: str, f: dict, built, name: str) -> list[str]:
    """MD-04..MD-08 for one field. `built` = aware pack_built_at or None (then time comparisons are skipped: the
    header already carries an MD-04 error, so the document fails anyway)."""
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


def validate_document(doc, name: str = "<doc>") -> list[str]:
    """document-level checks of one pack JSON (ticker_snapshot or pack_index): MD-03 schema, MD-04..MD-10.
    Cross-file rules (MD-01-DATE, MD-02, MD-11, MD-12) need the whole pack: market_data_loader.load_pack."""
    if not isinstance(doc, dict):
        return [f"MD-03-JSON {name}: a JSON {type(doc).__name__}, not an object"]
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


def pack_errors(index: dict, snapshots: dict[str, dict], dir_name: str) -> list[str]:
    """cross-file rules for a parsed pack: MD-01-DATE, MD-11, MD-12. `snapshots` = {file name: parsed ticker doc}."""
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
                errs = mdl.load_pack(p).errors
            else:
                try:
                    doc = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, ValueError) as ex:
                    errs = [f"MD-03-JSON {p}: cannot read/parse: {type(ex).__name__}: {ex}"]
                else:
                    errs = validate_document(doc, p.name)
        except Exception as ex:  # noqa: BLE001 - a crashing validator must never look like a pass
            errs = [f"MD-00-CRASH {p}: validator raised {type(ex).__name__}: {ex}"]
        if errs:
            print("\n".join(f"{p}: {x}" for x in errs), file=sys.stderr if exit_code(errs) == 2 else sys.stdout)
        else:
            print(f"OK: {p} valid (market_snapshot {RULES['market_data_rules_version']}, " + ("pack rules MD-00..MD-12)" if p.is_dir() else "document rules MD-03..MD-10)"))
        rc = max(rc, exit_code(errs))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
