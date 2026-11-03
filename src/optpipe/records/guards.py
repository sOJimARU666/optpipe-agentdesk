"""optpipe-side record guards on top of Doodle's validate_session.py (FIX-SEC-001, AUDIT-001B P0).

PAPER ONLY. These checks never change a record; they only report errors. They are the spec 1.1.3 checks
(spec/session.schema.json x-invariants INV-05 / INV-06 / INV-16, spec/validate_session.py approval_errors /
position_errors / overall_errors) applied by optpipe to EVERY record it reads, whatever its schema_version:
Doodle's validator gates them to 1.1.3-1.1.6 records (1.0.0-1.1.2 validate exactly as before), but optpipe's
consumers (watch, dashboard, idea log, summaries) must never act on a tampered or unacknowledged older record.
Error strings use the spec's ids and wording, so for a 1.1.3+ record they equal the validator's (merge() dedupes).

  INV-16-VERDICT / -BLOCKING / -FLAGS / -EMPTY   stored overall != risk_reference.overall(results) (ids as sets)
  INV-05-VETO / -FOREIGN / -ACK-MISSING / -ACK-EXTRA / -ACK-DUPLICATE / -TERMS / -EXPIRY
  INV-06-STATUS / -STATUS-UNDATED / -TICKET / -TERMS / -VETO / -BEFORE / -EXPIRED

Approval state comes only from `status` (no `decision` field). A non-active approval (revoked / expired /
consumed) is valid at an opening fill only if `status_changed_at` is strictly AFTER the fill (changed <= fill is
invalid, the same rule for every non-active status); a non-active approval without `status_changed_at` is invalid.
Flag acknowledgement must be an exact match: an absent `acknowledged_flags` acknowledges nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

BLOCKING, FLAGGING = "blocking", "flag"


def classify(result: dict) -> str | None:
    """blocking | flag | None for one rule result (risk_reference.overall / risk.engine.overall table)."""
    res, sev = result.get("result"), result.get("severity")
    if res == "VETO" or (res == "UNKNOWN" and sev == "hard"):
        return BLOCKING
    if res == "FLAG" or (res == "UNKNOWN" and sev == "soft"):
        return FLAGGING
    return None


def derive_overall(results: Iterable[dict]) -> dict[str, Any]:
    """risk_reference.overall(results): {verdict, blocking_rule_ids, flag_rule_ids}. No results at all is not a
    PASS (spec 1.1.3: combine([]) is UNKNOWN): verdict 'UNKNOWN', which every consumer treats as blocking."""
    results = list(results or [])
    if not results:
        return {"verdict": "UNKNOWN", "blocking_rule_ids": [], "flag_rule_ids": []}
    blocking = [r.get("rule_id") for r in results if classify(r) == BLOCKING]
    flags = [r.get("rule_id") for r in results if classify(r) == FLAGGING]
    return {"verdict": "VETO" if blocking else ("FLAG" if flags else "PASS"),
            "blocking_rule_ids": blocking, "flag_rule_ids": flags}


def review_is_veto(rv: dict) -> bool:
    """validate_session.review_is_veto: recorded VETO, results give VETO, or no results (fail closed)."""
    results = rv.get("results") or []
    return (not results or (rv.get("overall") or {}).get("verdict") == "VETO"
            or derive_overall(results)["verdict"] == "VETO")


def effective_verdict(rv: dict) -> str:
    """The verdict a consumer may act on: VETO unless the stored overall is exactly what the results give."""
    return "VETO" if review_is_veto(rv) or review_errors(rv) else (rv.get("overall") or {}).get("verdict")


def _dups(ids) -> list:
    seen, out = set(), []
    for i in ids:
        if i in seen and i not in out:
            out.append(i)
        seen.add(i)
    return out


def review_errors(rv: dict) -> list[str]:
    """INV-16 errors for one risk review (empty when the stored overall equals what its results give)."""
    rid, got = rv.get("review_id", "?"), rv.get("overall") or {}
    results = rv.get("results") or []
    if not results:
        return [f"INV-16-EMPTY {rid} has no rule results; the aggregate is UNKNOWN (fail closed), no stored verdict is valid"]
    want, out = derive_overall(results), []
    if got.get("verdict") != want["verdict"]:
        out.append(f"INV-16-VERDICT {rid} overall.verdict {got.get('verdict')} but rule results give {want['verdict']}")
    for key, code in (("blocking_rule_ids", "INV-16-BLOCKING"), ("flag_rule_ids", "INV-16-FLAGS")):
        ids = list(got.get(key) or [])
        if set(ids) != set(want[key]) or len(ids) != len(set(ids)):
            out.append(f"{code} {rid} overall.{key} {sorted(ids)} but rule results give {sorted(want[key])}")
    return out


def required_acks(rv: dict) -> set[str]:
    """Every flagged rule_id of a review: FLAG / soft UNKNOWN results plus the recorded flag_rule_ids."""
    return set(derive_overall(rv.get("results") or [])["flag_rule_ids"]) | set((rv.get("overall") or {}).get("flag_rule_ids") or [])


def _ts(pair: dict | None) -> datetime | None:
    try:
        return datetime.fromisoformat(pair["ny"]) if pair else None
    except (KeyError, TypeError, ValueError):
        return None


def reviews_by_time(reviews: Iterable[dict]) -> list[dict]:
    """Reviews oldest -> newest by `evaluated_at` (NY instant), as spec/watcher_reference.target_from_session sorts
    them. The sort is stable, so reviews with the same instant keep their array order and the one listed last is the
    latest (the reference's tie-break). Undated reviews (schema-invalid; the reference cannot order them) sort first,
    among themselves by review_id, so their order does not depend on the array order; callers that must fail closed
    check them separately (watcher/runtime.py)."""
    rv = list(reviews)
    lo = datetime.min.replace(tzinfo=timezone.utc)

    def key(r: dict):
        t = _ts(r.get("evaluated_at"))
        return (True, t, "") if t is not None else (False, lo, str(r.get("review_id") or ""))
    return sorted(rv, key=key)


def latest_review(reviews: Iterable[dict]) -> dict | None:
    """The latest review by `evaluated_at` (never by list position); None when there is none."""
    rv = reviews_by_time(reviews)
    return rv[-1] if rv else None


def _terms_differ(a: dict, tk: dict) -> bool:
    return a.get("legs") != tk.get("legs") or a.get("quantity") != tk.get("quantity") or a.get("limit_price") != tk.get("limit_price")


def approval_errors(a: dict, rv: dict | None, tk: dict | None = None) -> list[str]:
    """INV-05 (spec 1.1.3 ids) for one approval against its review (and ticket, when given)."""
    aid, out = a.get("approval_id", "?"), []
    if rv is not None:
        if review_is_veto(rv):
            out.append(f"INV-05-VETO {aid} approves review {rv.get('review_id')} whose verdict is VETO "
                       f"(recorded {(rv.get('overall') or {}).get('verdict')}, rule results give "
                       f"{derive_overall(rv.get('results') or [])['verdict']})")
        if rv.get("ticket_id") != a.get("ticket_id"):
            out.append(f"INV-05-FOREIGN {aid} is for ticket {a.get('ticket_id')} but review {rv.get('review_id')} is for {rv.get('ticket_id')}")
        if not review_is_veto(rv):
            need, got = required_acks(rv), list(a.get("acknowledged_flags") or [])
            if need - set(got):
                out.append(f"INV-05-ACK-MISSING {aid} does not acknowledge flagged rule_id(s) {sorted(need - set(got))} of {rv.get('review_id')}")
            if set(got) - need:
                out.append(f"INV-05-ACK-EXTRA {aid} acknowledges rule_id(s) {sorted(set(got) - need)} that {rv.get('review_id')} did not flag")
            if _dups(got):
                out.append(f"INV-05-ACK-DUPLICATE {aid} acknowledged_flags lists {_dups(got)} more than once")
    if tk is not None and _terms_differ(a, tk):
        out.append(f"INV-05-TERMS {aid} legs/qty/limit differ from ticket {tk.get('ticket_id')}")
    t0, t1 = _ts(a.get("approved_at")), _ts(a.get("expires_at"))
    if t0 is None or t1 is None or t1 <= t0:
        out.append(f"INV-05-EXPIRY {aid} expires_at {(a.get('expires_at') or {}).get('ny')} is not after approved_at "
                   f"{(a.get('approved_at') or {}).get('ny')}")
    return out


def approval_active_at(ap: dict, when: datetime) -> bool:
    """Was this approval still `active` at `when`? `active`, or a non-active status whose `status_changed_at` is
    strictly after `when` (changed <= when is invalid for revoked, expired and consumed alike; undated is invalid)."""
    if ap.get("status") == "active":
        return True
    changed = _ts(ap.get("status_changed_at"))
    return changed is not None and when < changed


def position_errors(p: dict, ap: dict | None, tk: dict | None = None, rv: dict | None = None) -> list[str]:
    """INV-06 (spec 1.1.3 ids) for one paper position against its approval (status, ticket, terms, review, window)."""
    if ap is None:
        return []          # unresolved approval_id: the spec's INV-04 reports it
    pid, aid, out = p.get("position_id", "?"), ap.get("approval_id", "?"), []
    opens = [f for f in p.get("fills") or [] if str(f.get("action", "")).endswith("_open")]
    if ap.get("status") != "active":
        changed = _ts(ap.get("status_changed_at"))
        if changed is None:
            out.append(f"INV-06-STATUS-UNDATED {pid} approval {aid} status {ap.get('status')!r} without status_changed_at")
        else:
            for f in opens:
                ft = _ts(f.get("filled_at"))
                if ft is None or changed <= ft:
                    out.append(f"INV-06-STATUS {f.get('fill_id', '?')} filled at {(f.get('filled_at') or {}).get('ny')} but approval "
                               f"{aid} became {ap.get('status')!r} at {ap['status_changed_at']['ny']}")
    if p.get("ticket_id") != ap.get("ticket_id"):
        out.append(f"INV-06-TICKET {pid} is for ticket {p.get('ticket_id')} but approval {aid} is for {ap.get('ticket_id')}")
    if tk is not None and _terms_differ(ap, tk):
        out.append(f"INV-06-TERMS {pid} approval {aid} legs/qty/limit differ from ticket {tk.get('ticket_id')}")
    if rv is not None and review_is_veto(rv):
        out.append(f"INV-06-VETO {pid} approval {aid} approves VETO review {rv.get('review_id')}")
    t0, t1 = _ts(ap.get("approved_at")), _ts(ap.get("expires_at"))
    for f in opens:
        ft = _ts(f.get("filled_at"))
        if ft is None or t0 is None or ft < t0:
            out.append(f"INV-06-BEFORE {f.get('fill_id', '?')} filled at {(f.get('filled_at') or {}).get('ny')} before approval "
                       f"{aid} at {(ap.get('approved_at') or {}).get('ny')}")
        elif t1 is None or ft > t1:
            out.append(f"INV-06-EXPIRED {f.get('fill_id', '?')} filled at {(f.get('filled_at') or {}).get('ny')} after approval "
                       f"{aid} expiry {(ap.get('expires_at') or {}).get('ny')}")
    return out


def check_record(rec: dict) -> list[str]:
    """All optpipe-side guard errors for a session record (any 1.x schema_version). [] when clean."""
    errs: list[str] = []
    reviews = {r.get("review_id"): r for r in rec.get("risk_reviews") or []}
    tickets = {t.get("ticket_id"): t for t in rec.get("tickets") or []}
    approvals = {a.get("approval_id"): a for a in rec.get("approvals") or []}
    for rv in rec.get("risk_reviews") or []:
        errs += review_errors(rv)
    for a in rec.get("approvals") or []:
        errs += approval_errors(a, reviews.get(a.get("review_id")), tickets.get(a.get("ticket_id")))
    for p in rec.get("paper_positions") or []:
        ap = approvals.get(p.get("approval_id"))
        errs += position_errors(p, ap, tickets.get(p.get("ticket_id")), reviews.get((ap or {}).get("review_id")))
    return errs


def merge(*lists: Iterable[str]) -> list[str]:
    """Concatenate error lists, dropping exact duplicates (a 1.1.3+ record gets the same strings from both)."""
    return list(dict.fromkeys(e for lst in lists for e in lst))
