"""optpipe-side record guards, run on top of Doodle's validate_session.py (FIX-SEC-001, AUDIT-001B P0).

PAPER ONLY. These checks never change a record; they only report errors. They close gaps the pinned spec
validator does not enforce yet (proposed to Doodle as a spec patch; until it lands, optpipe enforces them here):

  * OPT-VERDICT  a risk review's overall verdict / blocking_rule_ids / flag_rule_ids must equal what its own
                 rule results give (risk_rules.md: VETO or hard UNKNOWN blocks, FLAG or soft UNKNOWN flags).
                 So no PASS/FLAG overall while a rule result vetoes, and no approval of such a review.
  * OPT-ACK      an approval of a review with flags must acknowledge every flagged rule_id, exactly
                 (spec/risk_rules.md "every flag must be acknowledged"; risk_rules.yaml approvable_verdicts).
  * OPT-INV-06   a paper position's approval must have status `active` at every opening fill (spec/session.schema.json INV-06 text:
                 "requires an approval with status active at every fill time"), and belong to the same ticket.
                 A non-active approval passes only if `status_changed_at` proves it was still active at each
                 opening fill (consumed: fill <= change; revoked/expired: fill < change).

Error strings start with the check id, like the spec's `INV-xx ...` messages.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

BLOCKING, FLAGGING = "blocking", "flag"


def classify(result: dict) -> str | None:
    """blocking | flag | None for one rule result (same table as risk.engine.overall)."""
    res, sev = result.get("result"), result.get("severity")
    if res == "VETO" or (res == "UNKNOWN" and sev == "hard"):
        return BLOCKING
    if res == "FLAG" or (res == "UNKNOWN" and sev == "soft"):
        return FLAGGING
    return None


def derive_overall(results: Iterable[dict]) -> dict[str, Any]:
    """The overall a review's rule results imply: {verdict, blocking_rule_ids, flag_rule_ids}."""
    results = list(results or [])
    blocking = [r.get("rule_id") for r in results if classify(r) == BLOCKING]
    flags = [r.get("rule_id") for r in results if classify(r) == FLAGGING]
    return {"verdict": "VETO" if blocking else ("FLAG" if flags else "PASS"),
            "blocking_rule_ids": blocking, "flag_rule_ids": flags}


def review_errors(rv: dict) -> list[str]:
    """OPT-VERDICT errors for one risk review (empty when consistent)."""
    rid = rv.get("review_id", "?")
    o = rv.get("overall") or {}
    d = derive_overall(rv.get("results") or [])
    errs = []
    if o.get("verdict") != d["verdict"]:
        errs.append(f"OPT-VERDICT {rid} overall verdict {o.get('verdict')!r} but rule results give {d['verdict']!r}")
    for k in ("blocking_rule_ids", "flag_rule_ids"):
        if set(o.get(k) or []) != set(d[k]):
            errs.append(f"OPT-VERDICT {rid} {k} {sorted(o.get(k) or [])} but rule results give {sorted(d[k])}")
    if o.get("approval_required") is not True:
        errs.append(f"OPT-VERDICT {rid} approval_required must be true")
    return errs


def required_acks(rv: dict) -> set[str]:
    """Every flagged rule_id of a review: the recorded flags plus those its results imply."""
    return set((rv.get("overall") or {}).get("flag_rule_ids") or []) | set(derive_overall(rv.get("results") or [])["flag_rule_ids"])


def approval_errors(a: dict, rv: dict | None) -> list[str]:
    """OPT-ACK / OPT-VERDICT errors for one approval against the review it approves."""
    if rv is None:
        return []          # unresolved review_id: the spec's INV-04 reports it
    aid = a.get("approval_id", "?")
    errs = []
    if derive_overall(rv.get("results") or [])["verdict"] == "VETO":
        errs.append(f"OPT-VERDICT {aid} approves review {rv.get('review_id')} whose rule results give VETO")
    need, got = required_acks(rv), set(a.get("acknowledged_flags") or [])
    if need - got:
        errs.append(f"OPT-ACK {aid} does not acknowledge flagged rule(s) {sorted(need - got)}")
    if got - need:
        errs.append(f"OPT-ACK {aid} acknowledges rule(s) {sorted(got - need)} that review {rv.get('review_id')} did not flag")
    return errs


def _ts(pair: dict | None) -> datetime | None:
    try:
        return datetime.fromisoformat(pair["ny"]) if pair else None
    except (KeyError, TypeError, ValueError):
        return None


def approval_active_at(ap: dict, when: datetime) -> bool:
    """Was this approval's status `active` at `when`? Only `active`, or a later recorded status change, proves it."""
    st = ap.get("status")
    if st == "active":
        return True
    changed = _ts(ap.get("status_changed_at"))
    if changed is None:
        return False
    return when <= changed if st == "consumed" else when < changed


def position_errors(p: dict, ap: dict | None) -> list[str]:
    """OPT-INV-06 errors for one paper position against its approval."""
    if ap is None:
        return []          # unresolved approval_id: the spec's INV-04 reports it
    pid = p.get("position_id", "?")
    errs = []
    if p.get("ticket_id") != ap.get("ticket_id"):
        errs.append(f"OPT-INV-06 {pid} ticket {p.get('ticket_id')} but approval {ap.get('approval_id')} "
                    f"is for {ap.get('ticket_id')}")
    for f in p.get("fills") or []:
        if not str(f.get("action", "")).endswith("_open"):
            continue
        ft = _ts(f.get("filled_at"))
        if ft is None or not approval_active_at(ap, ft):
            errs.append(f"OPT-INV-06 {f.get('fill_id', '?')} filled while approval {ap.get('approval_id')} "
                        f"was not active (status {ap.get('status')!r})")
    return errs


def check_record(rec: dict) -> list[str]:
    """All optpipe-side guard errors for a session record (spec 1.1.x shape). [] when clean."""
    errs: list[str] = []
    reviews = {r.get("review_id"): r for r in rec.get("risk_reviews") or []}
    for rv in rec.get("risk_reviews") or []:
        errs += review_errors(rv)
    for a in rec.get("approvals") or []:
        errs += approval_errors(a, reviews.get(a.get("review_id")))
    approvals = {a.get("approval_id"): a for a in rec.get("approvals") or []}
    for p in rec.get("paper_positions") or []:
        errs += position_errors(p, approvals.get(p.get("approval_id")))
    return errs
