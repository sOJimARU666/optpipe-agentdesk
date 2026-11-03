"""Validate a session record (schema v1.0.0 or v1.1.0): JSON Schema (draft 2020-12) + x-invariants INV-01..13.
Usage: python validate_session.py session.example.json [more.json ...]"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

import risk_reference as rr

SPEC = Path(__file__).resolve().parent
NY, BERLIN = ZoneInfo("America/New_York"), ZoneInfo("Europe/Berlin")
SCHEMA = json.loads((SPEC / "session.schema.json").read_text())
CAL = rr.Calendar(rr.load_rules()["config"]["market_hours"])
TICKS = rr.load_rules()["config"]["tick_rules"]
_MODE = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_UP}


def tick_for(underlying: str, raw) -> Decimal:
    """INV-13 tick grid: penny-pilot 0.01 below $3 / 0.05 at or above; others 0.05 / 0.10 (tier by raw price)."""
    lo, hi = TICKS["penny_ticks"] if underlying.upper() in {s.upper() for s in TICKS["penny_pilot"]} else TICKS["standard_ticks"]
    return Decimal(lo) if abs(Decimal(repr(raw))) < Decimal(TICKS["threshold"]) else Decimal(hi)


def round_tick(raw, tick, direction) -> Decimal:
    """round(raw, tick, direction) in exact decimal (repr keeps 3.1006 exact)."""
    raw = Decimal(repr(raw))
    if direction == "none":
        return raw
    q = Decimal(repr(tick)) if not isinstance(tick, Decimal) else tick
    return (raw / q).to_integral_value(rounding=_MODE[direction]) * q


def _walk(o, path=""):
    yield path, o
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk(v, f"{path}/{i}")


def is_field(o):
    return isinstance(o, dict) and {"value", "source_id", "as_of", "status"} <= o.keys()


def is_tspair(o):
    return isinstance(o, dict) and set(o) == {"ny", "berlin"}


def quality_index(sess):
    out = {"missing": [], "stale": [], "conflicting": []}
    for p, o in _walk(sess):
        if p.startswith("/data_quality") or p.startswith("/risk_reviews"):
            continue
        if is_field(o) and o["status"] != "ok":
            out[o["status"]].append(p)
    return out


def validate(sess: dict, base_dir: Path | None = None) -> list[str]:
    """base_dir: directory of the record file; enables the INV-11 trace file/hash check."""
    v = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
    errs = [f"schema {'/'.join(map(str, e.absolute_path))}: {e.message[:200]}" for e in v.iter_errors(sess)]
    if errs:
        return errs
    src = {s["id"]: s for s in sess["sources"]}
    # INV-01 source refs, INV-02 tspairs, INV-09 as_of <= fetched_at
    for p, o in _walk(sess):
        if isinstance(o, dict):
            for k in ("source_id", "quote_source_id"):
                if isinstance(o.get(k), str) and o[k] not in src:
                    errs.append(f"INV-01 {p}/{k} unknown source {o[k]}")
            if is_tspair(o):
                ny, be = datetime.fromisoformat(o["ny"]), datetime.fromisoformat(o["berlin"])
                if ny != be:
                    errs.append(f"INV-02 {p} ny/berlin differ")
                if ny.utcoffset() != ny.astimezone(NY).utcoffset():
                    errs.append(f"INV-02 {p} wrong NY offset for that date")
                if be.utcoffset() != be.astimezone(BERLIN).utcoffset():
                    errs.append(f"INV-02 {p} wrong Berlin offset for that date")
            if is_field(o) and o["status"] in ("ok", "stale") and o["source_id"] in src:
                if datetime.fromisoformat(o["as_of"]) > datetime.fromisoformat(src[o["source_id"]]["fetched_at"]):
                    errs.append(f"INV-09 {p} as_of after source fetch")
    v11 = sess["schema_version"] == "1.1.0"
    # INV-10 run phase vs NY clock (v1.1 'closed')
    for r in sess["runs"]:
        closed = CAL.is_closed(datetime.fromisoformat(r["started"]["ny"]))
        if r["phase"] == "closed" and not v11:
            errs.append(f"INV-10 run {r['run_id']} phase 'closed' requires schema_version 1.1.0")
        elif r["phase"] == "closed" and not closed:
            errs.append(f"INV-10 run {r['run_id']} phase 'closed' but started {r['started']['ny']} is inside NY 04:00-close on a trading day")
        elif v11 and closed and r["phase"] != "closed":
            errs.append(f"INV-10 run {r['run_id']} started {r['started']['ny']} in the NY closed window but phase is {r['phase']!r}")
    # INV-11 trace_ref (v1.1)
    tr = sess.get("trace_ref")
    if tr is not None:
        if not v11:
            errs.append("INV-11 trace_ref requires schema_version 1.1.0")
        elif base_dir is not None:
            base = Path(base_dir).resolve()
            f = (base / tr["path"]).resolve()
            if base not in f.parents:
                errs.append(f"INV-11 trace_ref.path {tr['path']} escapes the record directory")
            elif not f.is_file():
                errs.append(f"INV-11 trace_ref.path {tr['path']} not found")
            elif hashlib.sha256(f.read_bytes()).hexdigest() != tr["sha256"]:
                errs.append(f"INV-11 trace_ref.sha256 mismatch for {tr['path']}")
    # INV-03 uniqueness
    def uniq(items, key, label):
        ids = [i[key] for i in items]
        if len(ids) != len(set(ids)):
            errs.append(f"INV-03 duplicate {label}")
        return set(ids)
    uniq(sess["sources"], "id", "source id")
    runs = uniq(sess["runs"], "run_id", "run_id")
    cands = uniq(sess["candidates"], "candidate_id", "candidate_id")
    tickets = uniq(sess["tickets"], "ticket_id", "ticket_id")
    reviews = uniq(sess["risk_reviews"], "review_id", "review_id")
    approvals = uniq(sess["approvals"], "approval_id", "approval_id")
    positions = uniq(sess["paper_positions"], "position_id", "position_id")
    watches = uniq(sess["watcher_rules"], "watch_id", "watch_id")
    uniq(sess["alerts"], "alert_id", "alert_id")
    uniq(sess["idea_log"], "idea_id", "idea_id")
    events = uniq(sess["event_calendar"]["events"], "event_id", "event_id")
    # INV-04 references
    def ref(val, pool, label):
        if val is not None and val not in pool:
            errs.append(f"INV-04 {label} -> {val} unresolved")
    ref(sess["premarket_scan"]["run_id"], runs, "premarket_scan.run_id")
    ref(sess["global_snapshot"]["run_id"], runs, "global_snapshot.run_id")
    for r in sess["premarket_scan"]["earnings_reports"]:
        ref(r["run_id"], runs, "earnings_reports.run_id")
    contracts_by_cand = {}
    for c in sess["candidates"]:
        for e in c.get("related_event_ids", []):
            ref(e, events, "candidate.related_event_ids")
        ids = set()
        for s in c["snapshots"]:
            ref(s["run_id"], runs, "snapshot.run_id")
            for sl in s["option_chain_slices"]:
                ids |= {k["contract_id"] for k in sl["contracts"]}
        contracts_by_cand[c["candidate_id"]] = ids
    tk_by_id = {t["ticket_id"]: t for t in sess["tickets"]}
    for t in sess["tickets"]:
        ref(t["candidate_id"], cands, "ticket.candidate_id")
        ref(t["pricing"]["run_id"], runs, "ticket.pricing.run_id")
        for e in t["intended_event_ids"]:
            ref(e, events, "ticket.intended_event_ids")
        for a in t["dm_alternatives"]:
            ref(a.get("ticket_id"), tickets, "dm_alternatives.ticket_id")
        for l in t["legs"]:  # INV-08
            if l["contract_id"] not in contracts_by_cand.get(t["candidate_id"], set()):
                errs.append(f"INV-08 {t['ticket_id']} leg {l['contract_id']} not in candidate chain")
    rv_by_id = {r["review_id"]: r for r in sess["risk_reviews"]}
    for r in sess["risk_reviews"]:
        ref(r["ticket_id"], tickets, "review.ticket_id"); ref(r["run_id"], runs, "review.run_id")
    # INV-05 approvals
    for a in sess["approvals"]:
        ref(a["ticket_id"], tickets, "approval.ticket_id"); ref(a["review_id"], reviews, "approval.review_id")
        rv, tk = rv_by_id.get(a["review_id"]), tk_by_id.get(a["ticket_id"])
        if rv and (rv["overall"]["verdict"] == "VETO" or rv["ticket_id"] != a["ticket_id"]):
            errs.append(f"INV-05 {a['approval_id']} approves a VETO/foreign review")
        if tk and (a["legs"] != tk["legs"] or a["quantity"] != tk["quantity"] or a["limit_price"] != tk["limit_price"]):
            errs.append(f"INV-05 {a['approval_id']} legs/qty/limit differ from ticket")
        if datetime.fromisoformat(a["expires_at"]["ny"]) <= datetime.fromisoformat(a["approved_at"]["ny"]):
            errs.append(f"INV-05 {a['approval_id']} expires before approval")
    # INV-06 positions
    ap_by_id = {a["approval_id"]: a for a in sess["approvals"]}
    for p in sess["paper_positions"]:
        ref(p["ticket_id"], tickets, "position.ticket_id"); ref(p["approval_id"], approvals, "position.approval_id")
        ap = ap_by_id.get(p["approval_id"])
        if ap:
            for f in p["fills"]:
                ft = datetime.fromisoformat(f["filled_at"]["ny"])
                if f["action"].endswith("_open") and not (datetime.fromisoformat(ap["approved_at"]["ny"]) <= ft <= datetime.fromisoformat(ap["expires_at"]["ny"])):
                    errs.append(f"INV-06 {f['fill_id']} filled outside approval validity")
            opens = [f for f in p["fills"] if f["action"].endswith("_open")]
            if opens:
                net = sum((f["price"] if f["action"] == "buy_to_open" else -f["price"]) * f["qty"] for f in opens) / ap["quantity"]
                lim = ap["limit_price"]
                if (lim["price_type"] == "debit" and net > lim["amount"] + 1e-9) or (lim["price_type"] == "credit" and -net < lim["amount"] - 1e-9):
                    errs.append(f"INV-06 {p['position_id']} entry net {net} violates limit")
    for w in sess["watcher_rules"]:
        ref(w["target"]["ticket_id"], tickets, "watch.ticket_id"); ref(w["target"].get("position_id"), positions, "watch.position_id")
    for al in sess["alerts"]:
        ref(al["watch_id"], watches, "alert.watch_id")
    # INV-07 idea log
    for i in sess["idea_log"]:
        ref(i.get("ticket_id"), tickets, "idea.ticket_id")
        times = [datetime.fromisoformat(h["at"]["ny"]) for h in i["history"]]
        if times != sorted(times):
            errs.append(f"INV-07 {i['idea_id']} history not chronological")
        if i["history"][-1]["status"] != i["current_status"]:
            errs.append(f"INV-07 {i['idea_id']} current_status != last history status")
    # INV-12 per-run premarket_scans[] / global_snapshots[] (v1.1)
    for arr, single in (("premarket_scans", "premarket_scan"), ("global_snapshots", "global_snapshot")):
        if arr not in sess:
            continue
        if not v11:
            errs.append(f"INV-12 {arr} requires schema_version 1.1.0")
            continue
        ids = [x["run_id"] for x in sess[arr]]
        if len(ids) != len(set(ids)):
            errs.append(f"INV-12 {arr} has duplicate run_id")
        for i in ids:
            if i not in runs:
                errs.append(f"INV-12 {arr} run_id {i} does not reference an existing run")
        if single in sess and sess[single]["run_id"] not in ids:
            errs.append(f"INV-12 {single}.run_id {sess[single]['run_id']} not in {arr}")
    # INV-13 raw tick fields (v1.1): tick per rule, rounded == round(raw, tick, direction), safe directions
    def tick_check(where, underlying, rounded, raw, tick, direction, need=None):
        if not v11:
            errs.append(f"INV-13 {where} raw tick fields require schema_version 1.1.0")
            return
        if need and direction != need:
            errs.append(f"INV-13 {where} rounding {direction!r}, must be {need!r}")
        if direction != "none" and Decimal(repr(tick)) != tick_for(underlying, raw):
            errs.append(f"INV-13 {where} tick_size {tick} != {tick_for(underlying, raw)} for {underlying} at {raw}")
        if not isinstance(rounded, (int, float)) or isinstance(rounded, bool) or Decimal(repr(rounded)) != round_tick(raw, tick, direction):
            errs.append(f"INV-13 {where} rounded {rounded} != round({raw}, {tick}, {direction}) = {round_tick(raw, tick, direction)}")
    for t in sess["tickets"]:
        lp = t["limit_price"]
        if "raw_amount" in lp:
            tick_check(f"{t['ticket_id']}.limit_price", t["underlying"], lp["amount"], lp["raw_amount"], lp["tick_size"], lp["rounding"],
                       "down" if lp["price_type"] == "debit" else None)
        for grp, need in (("entry_rules", None), ("exit_rules", None), ("stop_rules", "up"), ("target_rules", "down")):
            for tr_ in t.get(grp, []):
                for j, c in enumerate(tr_["conditions"]):
                    if "raw_value" in c:
                        tick_check(f"{t['ticket_id']}.{grp}.{tr_['rule_id']}[{j}]", t["underlying"], c["value"], c["raw_value"], c["tick_size"], c["rounding"], need)
    for w in sess["watcher_rules"]:
        tk = tk_by_id.get(w["target"]["ticket_id"])
        for j, c in enumerate(w["conditions"]):
            if "raw_value" in c and tk:
                tick_check(f"watch {w['watch_id']}[{j}]", tk["underlying"], c["value"], c["raw_value"], c["tick_size"], c["rounding"])
    # data_quality must be regenerable
    qi = quality_index(sess)
    for k in ("missing", "stale", "conflicting"):
        if sorted(qi[k]) != sorted(sess["data_quality"][k]):
            errs.append(f"data_quality.{k} out of sync with record")
    return errs


if __name__ == "__main__":
    paths = [Path(a) for a in sys.argv[1:]] or [SPEC / "session.example.json"]
    bad = 0
    for path in paths:
        e = validate(json.loads(path.read_text()), base_dir=path.resolve().parent)
        print("\n".join(f"{path}: {x}" for x in e) if e else f"OK: {path} valid (schema + invariants)")
        bad += bool(e)
    sys.exit(1 if bad else 0)
