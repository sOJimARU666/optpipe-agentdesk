"""Validate a session record (schema v1.0.0, v1.1.0, v1.1.1, v1.1.2, v1.1.3 or v1.1.4): JSON Schema (draft 2020-12) + x-invariants INV-01..16.
Usage: python validate_session.py session.example.json [more.json ...]

Exit codes (fail-closed contract, see AGENTS.md / CHANGELOG 1.1.3):
  0  every file was read, parsed as a JSON object and validated with no errors
  1  at least one record has schema / invariant errors (an unknown schema_version is a schema error)
  2  at least one file could not be read or parsed, is not a JSON object, or the validator itself raised
Consumers must treat ANY non-zero exit, and a missing or unimportable validate_session.py, as FAIL.

1.1.3 records get the stricter checks (INV-03/04 tightening, INV-05 flag acknowledgement + specific error ids,
INV-06 approval status/ticket/terms checks, INV-16 overall-verdict consistency);
1.0.0-1.1.2 records validate exactly as before (same error strings).
1.1.4 adds no invariant: a 1.1.4 record gets exactly the 1.1.3 checks, and 1.1.3 records validate exactly as before."""
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
import verticals as vt

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
    v11 = sess["schema_version"] in ("1.1.0", "1.1.1", "1.1.2", "1.1.3", "1.1.4")   # v1.1 shape
    v111 = sess["schema_version"] in ("1.1.1", "1.1.2", "1.1.3", "1.1.4")   # stricter 1.1.1 checks (INV-13 credit, INV-14), kept in 1.1.2+
    v112 = sess["schema_version"] in ("1.1.2", "1.1.3", "1.1.4")          # 1.1.2 checks (INV-15 stop/target operators), kept in 1.1.3+
    v113 = sess["schema_version"] in ("1.1.3", "1.1.4")                   # 1.1.3 checks (INV-03/04/05/06 tightening, INV-16), kept in 1.1.4 (no new invariants)
    # INV-10 run phase vs NY clock (v1.1 'closed')
    for r in sess["runs"]:
        closed = CAL.is_closed(datetime.fromisoformat(r["started"]["ny"]))
        if r["phase"] == "closed" and not v11:
            errs.append(f"INV-10 run {r['run_id']} phase 'closed' requires schema_version 1.1.0, 1.1.1 or 1.1.2")
        elif r["phase"] == "closed" and not closed:
            errs.append(f"INV-10 run {r['run_id']} phase 'closed' but started {r['started']['ny']} is inside NY 04:00-close on a trading day")
        elif v11 and closed and r["phase"] != "closed":
            errs.append(f"INV-10 run {r['run_id']} started {r['started']['ny']} in the NY closed window but phase is {r['phase']!r}")
    # INV-11 trace_ref (v1.1)
    tr = sess.get("trace_ref")
    if tr is not None:
        if not v11:
            errs.append("INV-11 trace_ref requires schema_version 1.1.0, 1.1.1 or 1.1.2")
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
    if v113:  # INV-03 (1.1.3): legs per ticket, contracts per chain slice, fill ids
        errs.extend(_inv03_v113(sess))
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
        if v113:  # INV-16 overall == aggregate of rule results
            errs.extend(overall_errors(r))
    # INV-05 approvals
    for a in sess["approvals"]:
        ref(a["ticket_id"], tickets, "approval.ticket_id"); ref(a["review_id"], reviews, "approval.review_id")
        rv, tk = rv_by_id.get(a["review_id"]), tk_by_id.get(a["ticket_id"])
        if v113:  # INV-05 specific ids + flag acknowledgement
            errs.extend(approval_errors(a, rv, tk))
            continue
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
        if v113:  # INV-04 fill/leg_mark resolution + INV-06 (1.1.3)
            errs.extend(position_errors(p, ap, tk_by_id.get(p["ticket_id"]), rv_by_id))
            continue
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
            errs.append(f"INV-12 {arr} requires schema_version 1.1.0, 1.1.1 or 1.1.2")
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
            errs.append(f"INV-13 {where} raw tick fields require schema_version 1.1.0, 1.1.1 or 1.1.2")
            return
        if need and direction != need:
            errs.append(f"INV-13 {where} rounding {direction!r}, must be {need!r}")
        if direction != "none" and Decimal(repr(tick)) != tick_for(underlying, raw):
            errs.append(f"INV-13 {where} tick_size {tick} != {tick_for(underlying, raw)} for {underlying} at {raw}")
        if not isinstance(rounded, (int, float)) or isinstance(rounded, bool) or Decimal(repr(rounded)) != round_tick(raw, tick, direction):
            errs.append(f"INV-13 {where} rounded {rounded} != round({raw}, {tick}, {direction}) = {round_tick(raw, tick, direction)}")
    for t in sess["tickets"]:
        lp = t["limit_price"]
        credit111 = v111 and vt.is_vertical(t["strategy"]) and vt.VERTICALS[t["strategy"]][1] == "credit"
        # debit: limit down / stop up / target down; 1.1.1+ credit verticals: min credit up / stop down / target up
        need_lim = "up" if credit111 else ("down" if lp["price_type"] == "debit" else None)
        need_stop, need_tgt = ("down", "up") if credit111 else ("up", "down")
        if "raw_amount" in lp:
            tick_check(f"{t['ticket_id']}.limit_price", t["underlying"], lp["amount"], lp["raw_amount"], lp["tick_size"], lp["rounding"], need_lim)
        for grp, need in (("entry_rules", None), ("exit_rules", None), ("stop_rules", need_stop), ("target_rules", need_tgt)):
            for tr_ in t.get(grp, []):
                for j, c in enumerate(tr_["conditions"]):
                    if "raw_value" in c:
                        tick_check(f"{t['ticket_id']}.{grp}.{tr_['rule_id']}[{j}]", t["underlying"], c["value"], c["raw_value"], c["tick_size"], c["rounding"], need)
    for w in sess["watcher_rules"]:
        tk = tk_by_id.get(w["target"]["ticket_id"])
        for j, c in enumerate(w["conditions"]):
            if "raw_value" in c and tk:
                tick_check(f"watch {w['watch_id']}[{j}]", tk["underlying"], c["value"], c["raw_value"], c["tick_size"], c["rounding"])
    # INV-14 vertical consistency (1.1.1, 1.1.2 and 1.1.3)
    if v111:
        for t in sess["tickets"]:
            if vt.is_vertical(t["strategy"]):
                errs.extend(f"INV-14 {t['ticket_id']} {m}" for m in vertical_errors(t))
    # INV-15 vertical stop/target comparison direction (1.1.2 and 1.1.3)
    if v112:
        for t in sess["tickets"]:
            if vt.is_vertical(t["strategy"]):
                errs.extend(f"INV-15 {t['ticket_id']} {m}" for m in direction_errors(t))
    # data_quality must be regenerable
    qi = quality_index(sess)
    for k in ("missing", "stale", "conflicting"):
        if sorted(qi[k]) != sorted(sess["data_quality"][k]):
            errs.append(f"data_quality.{k} out of sync with record")
    return errs


# ------------------------------------------------------------------ 1.1.3 checks
def _ny(pair) -> datetime:
    return datetime.fromisoformat(pair["ny"])


def _dups(ids) -> list:
    seen, out = set(), []
    for i in ids:
        if i in seen and i not in out:
            out.append(i)
        seen.add(i)
    return out


def _inv03_v113(sess: dict) -> list[str]:
    """INV-03 (1.1.3): leg_id / contract_id unique within each ticket's legs; contract_id unique within each
    option_chain_slice; fill_id unique across all paper_positions."""
    out = []
    for t in sess["tickets"]:
        for key in ("leg_id", "contract_id"):
            out += [f"INV-03-LEG {t['ticket_id']} duplicate {key} {d}" for d in _dups(l[key] for l in t["legs"])]
    for c in sess["candidates"]:
        for s in c["snapshots"]:
            for j, sl in enumerate(s["option_chain_slices"]):
                out += [f"INV-03-CONTRACT {c['candidate_id']} snapshot {s['run_id']} slice {j} duplicate contract_id {d}"
                        for d in _dups(k["contract_id"] for k in sl["contracts"])]
    out += [f"INV-03-FILL duplicate fill_id {d}" for d in _dups(f["fill_id"] for p in sess["paper_positions"] for f in p["fills"])]
    return out


def review_is_veto(rv: dict) -> bool:
    """recorded overall verdict VETO, or the verdict the rule results give (risk_reference.overall) is VETO,
    or there are no rule results at all (fail closed: combine([]) is UNKNOWN)"""
    return not rv["results"] or rv["overall"]["verdict"] == "VETO" or rr.overall(rv["results"])["verdict"] == "VETO"


def overall_errors(rv: dict) -> list[str]:
    """INV-16 (1.1.3): overall must equal risk_reference.overall(results) (verdict exactly; id lists as sets).
    No rule results at all is an error (fail closed, like risk_reference.combine([]) == 'UNKNOWN')."""
    want, got, rid = rr.overall(rv["results"]), rv["overall"], rv["review_id"]
    if not rv["results"]:
        return [f"INV-16-EMPTY {rid} has no rule results; the aggregate is UNKNOWN (fail closed), no stored verdict is valid"]
    out = []
    if got["verdict"] != want["verdict"]:
        out.append(f"INV-16-VERDICT {rid} overall.verdict {got['verdict']} but rule results give {want['verdict']}")
    for key, code in (("blocking_rule_ids", "INV-16-BLOCKING"), ("flag_rule_ids", "INV-16-FLAGS")):
        if set(got[key]) != set(want[key]) or len(got[key]) != len(set(got[key])):
            out.append(f"{code} {rid} overall.{key} {sorted(got[key])} but rule results give {sorted(want[key])}")
    return out


def required_acks(rv: dict) -> set:
    """INV-05 (1.1.3) flag acknowledgement: flagged rule_ids of a review = FLAG or soft UNKNOWN results (risk_reference.overall) plus recorded flag_rule_ids."""
    return set(rr.overall(rv["results"])["flag_rule_ids"]) | set(rv["overall"]["flag_rule_ids"])


def approval_errors(a: dict, rv: dict | None, tk: dict | None) -> list[str]:
    """INV-05 (1.1.3: specific ids + flag acknowledgement) for one approval. Unresolved refs are reported by INV-04."""
    aid, out = a["approval_id"], []
    if rv is not None:
        if review_is_veto(rv):
            out.append(f"INV-05-VETO {aid} approves review {rv['review_id']} whose verdict is VETO "
                       f"(recorded {rv['overall']['verdict']}, rule results give {rr.overall(rv['results'])['verdict']})")
        if rv["ticket_id"] != a["ticket_id"]:
            out.append(f"INV-05-FOREIGN {aid} is for ticket {a['ticket_id']} but review {rv['review_id']} is for {rv['ticket_id']}")
        if not review_is_veto(rv):
            need, got = required_acks(rv), a.get("acknowledged_flags", [])
            if need - set(got):
                out.append(f"INV-05-ACK-MISSING {aid} does not acknowledge flagged rule_id(s) {sorted(need - set(got))} of {rv['review_id']}")
            if set(got) - need:
                out.append(f"INV-05-ACK-EXTRA {aid} acknowledges rule_id(s) {sorted(set(got) - need)} that {rv['review_id']} did not flag")
            if _dups(got):
                out.append(f"INV-05-ACK-DUPLICATE {aid} acknowledged_flags lists {_dups(got)} more than once")
    if tk is not None and _terms_differ(a, tk):
        out.append(f"INV-05-TERMS {aid} legs/qty/limit differ from ticket {tk['ticket_id']}")
    if _ny(a["expires_at"]) <= _ny(a["approved_at"]):
        out.append(f"INV-05-EXPIRY {aid} expires_at {a['expires_at']['ny']} is not after approved_at {a['approved_at']['ny']}")
    return out


def _terms_differ(a: dict, tk: dict) -> bool:
    return a["legs"] != tk["legs"] or a["quantity"] != tk["quantity"] or a["limit_price"] != tk["limit_price"]


def position_errors(p: dict, ap: dict | None, tk: dict | None, rv_by_id: dict) -> list[str]:
    """INV-04 fill / leg_mark resolution and INV-06 (1.1.3) for one paper position.
    ap / tk are the position's approval and ticket (None when unresolved: INV-04 reports that)."""
    pid, out = p["position_id"], []
    legs = {l["leg_id"]: l for l in tk["legs"]} if tk else None
    if legs is not None:
        for f in p["fills"]:
            if f["leg_id"] not in legs:
                out.append(f"INV-04-FILL-LEG {f['fill_id']} leg_id {f['leg_id']} is not a leg of ticket {tk['ticket_id']}")
            elif f["contract_id"] != legs[f["leg_id"]]["contract_id"]:
                out.append(f"INV-04-FILL-CONTRACT {f['fill_id']} contract_id {f['contract_id']} != {f['leg_id']} contract "
                           f"{legs[f['leg_id']]['contract_id']}")
        for m in p["marks"]:
            for lm in m.get("leg_marks", []):
                if lm["leg_id"] not in legs:
                    out.append(f"INV-04-MARK-LEG {pid} mark {m['marked_at']['ny']} leg_id {lm['leg_id']} is not a leg of ticket {tk['ticket_id']}")
    if ap is None:
        return out
    aid = ap["approval_id"]
    opens = [f for f in p["fills"] if f["action"].endswith("_open")]
    # active at every opening fill: status 'active', or revoked/expired/consumed with status_changed_at strictly AFTER
    # the fill (changed at or before the fill -> invalid; a non-active status with no status_changed_at -> invalid).
    # Closing fills reduce risk and are not gated by the entry approval (as for expires_at).
    if ap["status"] != "active":
        if "status_changed_at" not in ap:
            out.append(f"INV-06-STATUS-UNDATED {pid} approval {aid} status {ap['status']!r} without status_changed_at")
        else:
            out += [f"INV-06-STATUS {f['fill_id']} filled at {f['filled_at']['ny']} but approval {aid} became {ap['status']!r} at "
                    f"{ap['status_changed_at']['ny']}" for f in opens if _ny(ap["status_changed_at"]) <= _ny(f["filled_at"])]
    if p["ticket_id"] != ap["ticket_id"]:
        out.append(f"INV-06-TICKET {pid} is for ticket {p['ticket_id']} but approval {aid} is for {ap['ticket_id']}")
    if tk is not None and _terms_differ(ap, tk):
        out.append(f"INV-06-TERMS {pid} approval {aid} legs/qty/limit differ from ticket {tk['ticket_id']}")
    rv = rv_by_id.get(ap["review_id"])
    if rv is not None and review_is_veto(rv):
        out.append(f"INV-06-VETO {pid} approval {aid} approves VETO review {rv['review_id']}")
    t0, t1 = _ny(ap["approved_at"]), _ny(ap["expires_at"])
    for f in opens:
        ft = _ny(f["filled_at"])
        if ft < t0:
            out.append(f"INV-06-BEFORE {f['fill_id']} filled at {f['filled_at']['ny']} before approval {aid} at {ap['approved_at']['ny']}")
        elif ft > t1:
            out.append(f"INV-06-EXPIRED {f['fill_id']} filled at {f['filled_at']['ny']} after approval {aid} expiry {ap['expires_at']['ny']}")
    alegs = {l["leg_id"]: l for l in ap["legs"]}
    qty = {}
    for f in opens:
        l = alegs.get(f["leg_id"])
        if l is None:
            out.append(f"INV-06-QTY {f['fill_id']} leg_id {f['leg_id']} is not an approved leg of {aid}")
            continue
        if f["action"] != ("buy_to_open" if l["side"] == "buy" else "sell_to_open"):
            out.append(f"INV-06-QTY {f['fill_id']} action {f['action']} contradicts approved {l['side']} leg {l['leg_id']}")
        qty[l["leg_id"]] = qty.get(l["leg_id"], 0) + f["qty"]
    for lid, n in qty.items():
        if n > ap["quantity"] * alegs[lid]["ratio"]:
            out.append(f"INV-06-QTY {pid} opened {n} contracts on {lid}, approval {aid} allows {ap['quantity'] * alegs[lid]['ratio']}")
    if opens:
        net = sum((rr.D(f["price"]) if f["action"] == "buy_to_open" else -rr.D(f["price"])) * f["qty"] for f in opens) / Decimal(ap["quantity"])
        lim = ap["limit_price"]
        amt = rr.D(lim["amount"])
        if (lim["price_type"] == "debit" and net > amt) or (lim["price_type"] == "credit" and -net < amt):
            out.append(f"INV-06-LIMIT {pid} entry net {'debit' if net >= 0 else 'credit'} {abs(net)} violates approved "
                       f"{lim['price_type']} limit {amt}")
    return out


def _pv(t, grp):
    """position_value conditions (cost to close the spread) in stop_rules / target_rules; INV-14 needs exactly one, INV-15 checks its op"""
    cs = [c for r in t.get(grp, []) for c in r["conditions"] if c["metric"] == "position_value"]
    return cs


# INV-15: required op on the position_value (cost to close) condition, by price_type
DIRECTION_OPS = {"debit": {"stop": "lte", "target": "gte"}, "credit": {"stop": "gte", "target": "lte"}}
_WHY = {
    ("debit", "stop"): "a debit spread is stopped out when its value falls to the stop",
    ("debit", "target"): "a debit spread hits its target when its value rises to the target",
    ("credit", "stop"): "a credit spread is stopped out when the buy-back cost rises to the stop",
    ("credit", "target"): "a credit spread hits its target when the buy-back cost falls to the target",
}


def direction_errors(t: dict) -> list[str]:
    """INV-15: comparison operator of the single position_value stop / target condition (same lookup as INV-14).
    Debit verticals: stop lte, target gte. Credit verticals (value = cost to buy back): stop gte, target lte.
    A missing or duplicate condition is reported by INV-14 and skipped here."""
    ptype = vt.VERTICALS[t["strategy"]][1]
    out = []
    for kind, grp in (("stop", "stop_rules"), ("target", "target_rules")):
        cs = _pv(t, grp)
        if len(cs) != 1:
            continue
        op, want = cs[0]["op"], DIRECTION_OPS[ptype][kind]
        if op != want:
            out.append(f"{kind} position_value op is {op!r}, must be {want!r} for a {ptype} vertical ({t['strategy']}): "
                       f"{_WHY[(ptype, kind)]} (position_value {want} {cs[0]['value']})")
    return out


def vertical_errors(t: dict) -> list[str]:
    """INV-14: all values from legs + limit (worst acceptable price); exact Decimal, 0.01 tolerance on $ amounts."""
    mults = {l.get("multiplier", 100) for l in t["legs"]}
    if mults != {100}:
        return [f"leg multiplier must be 100, is {sorted(mults)}"]
    v = vt.compute(t["strategy"], t["legs"], t["quantity"], t["limit_price"], 100)
    if "error" in v:
        return [v["error"]]
    out, D, TOL = [], vt.D, Decimal("0.01")
    w, p, q, m = v["width"], v["price"], v["qty"], v["mult"]
    stops, tgts = _pv(t, "stop_rules"), _pv(t, "target_rules")
    if len(stops) != 1:
        out.append(f"needs exactly one position_value stop condition, has {len(stops)}")
    if len(tgts) != 1:
        out.append(f"needs exactly one position_value target condition, has {len(tgts)}")
    stop = D(stops[0]["value"]) if len(stops) == 1 else None
    tgt = D(tgts[0]["value"]) if len(tgts) == 1 else None
    if v["price_type"] == "credit":
        if not (0 < p < w):
            out.append(f"min credit {p} must satisfy 0 < credit < width {w}")
        if stop is not None and not (p < stop < w):
            out.append(f"stop debit {stop} must satisfy credit {p} < stop < width {w}")
        if tgt is not None and not (0 <= tgt < p):
            out.append(f"target buyback {tgt} must satisfy 0 <= target < credit {p}")
        if stop is not None and (stop - p) * m * q > v["max_loss"]:
            out.append(f"planned loss at stop {(stop - p) * m * q} > max_loss {v['max_loss']}")
    else:
        if not (0 < p < w):
            out.append(f"max debit {p} must satisfy 0 < debit < width {w}")
        if stop is not None and not (0 < stop < p):
            out.append(f"stop {stop} must satisfy 0 < stop < debit {p}")
        if tgt is not None and not (p < tgt <= w):
            out.append(f"target {tgt} must satisfy debit {p} < target <= width {w}")
    for key, want in (("max_loss", v["max_loss"]), ("max_profit", v["max_profit"])):
        st = t[key]
        if st.get("unlimited") or st.get("amount_usd") is None or abs(D(st["amount_usd"]) - want) > TOL:
            out.append(f"{key}.amount_usd {st.get('amount_usd')} != computed {want}")
        if st.get("at_price") is not None and D(st["at_price"]) != p:
            out.append(f"{key}.at_price {st['at_price']} != limit {p}")
    be_lim = v["breakeven_at"](p)
    if not any(D(b["at_price"]) == p for b in t["breakevens"]):
        out.append(f"no breakeven at the limit {p} (computed {be_lim})")
    for b in t["breakevens"]:
        want = v["breakeven_at"](b["at_price"])
        if abs(D(b["underlying_price"]) - want) > TOL:
            out.append(f"breakeven {b['underlying_price']} at {b['at_price']} != computed {want}")
    return out


def main(argv: list[str]) -> int:
    """CLI: 0 all valid; 1 any record invalid; 2 any file unreadable / unparsable / not an object, or the validator raised.
    Every file is still checked; the worst code wins. Fail closed: nothing is 'OK' unless it validated."""
    paths = [Path(a) for a in argv] or [SPEC / "session.example.json"]
    rc = 0
    for path in paths:
        try:
            sess = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as ex:
            print(f"{path}: ERROR cannot read/parse record: {type(ex).__name__}: {ex}", file=sys.stderr)
            rc = 2
            continue
        if not isinstance(sess, dict):
            print(f"{path}: ERROR record is a JSON {type(sess).__name__}, not an object", file=sys.stderr)
            rc = 2
            continue
        try:
            e = validate(sess, base_dir=path.resolve().parent)
        except Exception as ex:  # noqa: BLE001 - a crashing validator must never look like a pass
            print(f"{path}: ERROR validator raised {type(ex).__name__}: {ex}", file=sys.stderr)
            rc = 2
            continue
        print("\n".join(f"{path}: {x}" for x in e) if e else f"OK: {path} valid (schema + invariants)")
        if e:
            rc = max(rc, 1)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
