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
  INV-17-REUSE (spec 1.1.6; not for 1.1.3-1.1.5 records)                 one approval opens at most one position

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


def _results(rv: dict) -> list[dict] | None:
    """The rule results of a review; None when `results` is not an array of objects (fail closed: VETO)."""
    v = rv.get("results")
    if v is None:
        return []
    return v if isinstance(v, list) and all(isinstance(x, dict) for x in v) else None


def _overall(rv: dict) -> dict:
    v = rv.get("overall")
    return v if isinstance(v, dict) else {}


def review_is_veto(rv: dict) -> bool:
    """validate_session.review_is_veto: recorded VETO, results give VETO, or no results (fail closed). Results that
    are not an array of objects are VETO too (SOL-ACCEPT-001 G5)."""
    results = _results(rv)
    return (not results or _overall(rv).get("verdict") == "VETO"
            or derive_overall(results)["verdict"] == "VETO")


def effective_verdict(rv: dict) -> str:
    """The verdict a consumer may act on: VETO unless the stored overall is exactly what the results give."""
    return "VETO" if review_is_veto(rv) or review_errors(rv) else _overall(rv).get("verdict")


def _dups(ids) -> list:
    seen, out = set(), []
    for i in ids:
        if i in seen and i not in out:
            out.append(i)
        seen.add(i)
    return out


def review_errors(rv: dict) -> list[str]:
    """INV-16 errors for one risk review (empty when the stored overall equals what its results give)."""
    rid, got = rv.get("review_id", "?"), _overall(rv)
    results = _results(rv)
    if results is None:
        return [f"INV-16-TYPE {rid} results is not an array of rule-result objects; the aggregate is VETO (fail closed)"]
    out = []
    if rv.get("overall") is not None and not isinstance(rv.get("overall"), dict):
        out.append(f"INV-16-TYPE {rid} overall is a {type(rv.get('overall')).__name__}, not an object (fail closed)")
    if not results:
        return out + [f"INV-16-EMPTY {rid} has no rule results; the aggregate is UNKNOWN (fail closed), no stored verdict is valid"]
    want = derive_overall(results)
    if got.get("verdict") != want["verdict"]:
        out.append(f"INV-16-VERDICT {rid} overall.verdict {got.get('verdict')} but rule results give {want['verdict']}")
    for key, code in (("blocking_rule_ids", "INV-16-BLOCKING"), ("flag_rule_ids", "INV-16-FLAGS")):
        v = got.get(key)
        ids = list(v) if isinstance(v, list) else ([] if v is None else [repr(v)])
        if set(map(str, ids)) != set(map(str, want[key])) or len(ids) != len(set(map(str, ids))):
            out.append(f"{code} {rid} overall.{key} {sorted(map(str, ids))} but rule results give {sorted(map(str, want[key]))}")
    return out


def required_acks(rv: dict) -> set[str]:
    """Every flagged rule_id of a review: FLAG / soft UNKNOWN results plus the recorded flag_rule_ids."""
    stored = _overall(rv).get("flag_rule_ids")
    stored = [x for x in stored if isinstance(x, str)] if isinstance(stored, list) else []
    return set(derive_overall(_results(rv) or [])["flag_rule_ids"]) | set(stored)


def _time(v: Any) -> tuple[datetime | None, str | None]:
    """(aware datetime, None) for a usable TsPair; (None, problem) otherwise, problem in {missing, naive, bad}.
    Never raises (SOL-ACCEPT-001 G5): a naive or unparsable time is an error to report, not a crash."""
    if v is None:
        return None, "missing"
    if not isinstance(v, dict) or not isinstance(v.get("ny"), str):
        return None, "bad"
    try:
        t = datetime.fromisoformat(v["ny"])
    except ValueError:
        return None, "bad"
    if t.tzinfo is None or t.utcoffset() is None:
        return None, "naive"
    return t, None


def _ts(pair: Any) -> datetime | None:
    """The NY instant of a TsPair, or None when it is missing, unparsable or naive (no UTC offset): a naive time
    can never be compared with an aware one, so it is never returned (SOL-ACCEPT-001 G5)."""
    return _time(pair)[0]


def _ny(v: Any) -> Any:
    """For messages only: the `ny` string of a TsPair, else the raw value (never raises)."""
    return v.get("ny") if isinstance(v, dict) else v


def _time_errors(code: str, owner: str, fields: Iterable[tuple[str, Any]]) -> list[str]:
    out = []
    for name, v in fields:
        prob = _time(v)[1]
        if prob == "naive":
            out.append(f"{code}-TIME-NAIVE {owner} {name} {_ny(v)!r} has no UTC offset (naive time; fail closed)")
        elif prob == "bad":
            out.append(f"{code}-TIME-INVALID {owner} {name} {_ny(v)!r} is not an ISO-8601 TsPair (fail closed)")
    return out


def _dicts(v: Any) -> list[dict]:
    """The object entries of a list field; a non-list value is treated as empty (its type is reported separately)."""
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


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
                       f"(recorded {_overall(rv).get('verdict')}, rule results give "
                       f"{derive_overall(_results(rv) or [])['verdict']})")
        if rv.get("ticket_id") != a.get("ticket_id"):
            out.append(f"INV-05-FOREIGN {aid} is for ticket {a.get('ticket_id')} but review {rv.get('review_id')} is for {rv.get('ticket_id')}")
        if not review_is_veto(rv):
            acks = a.get("acknowledged_flags")
            need, got = required_acks(rv), ([str(x) for x in acks] if isinstance(acks, list) else [])
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
        out.append(f"INV-05-EXPIRY {aid} expires_at {_ny(a.get('expires_at'))} is not after approved_at "
                   f"{_ny(a.get('approved_at'))}")
    out += _time_errors("INV-05", aid, (("approved_at", a.get("approved_at")), ("expires_at", a.get("expires_at"))))
    if a.get("status") != "active" and a.get("status_changed_at") is not None:
        out += _time_errors("INV-05", aid, (("status_changed_at", a.get("status_changed_at")),))
    return out


def approval_active_at(ap: dict, when: datetime) -> bool:
    """Was this approval still `active` at `when`? `active`, or a non-active status whose `status_changed_at` is
    strictly after `when` (changed <= when is invalid for revoked, expired and consumed alike; undated is invalid)."""
    if ap.get("status") == "active":
        return True
    changed = _ts(ap.get("status_changed_at"))
    return changed is not None and when < changed


CLOSED_WINDOW_VERSIONS = frozenset({"1.1.3", "1.1.4", "1.1.5"})
"""Record versions whose frozen INV-06 window is closed (approved_at <= filled_at <= expires_at). Every other record
(1.1.6 and any version optpipe applies the guards to on its own) gets the spec 1.1.6 open window."""


def open_window(rec: Any) -> bool:
    """True when the spec 1.1.6 open INV-06 window applies to this record (SOL-ACCEPT-001 G4)."""
    return not (isinstance(rec, dict) and rec.get("schema_version") in CLOSED_WINDOW_VERSIONS)


def position_errors(p: dict, ap: dict | None, tk: dict | None = None, rv: dict | None = None,
                    open_window: bool = True) -> list[str]:
    """INV-06 (spec 1.1.3 ids) for one paper position against its approval (status, ticket, terms, review, window).

    Window (SOL-ACCEPT-001 G4, spec 1.1.6): by default an opening fill is valid only while approved_at < filled_at <
    expires_at (open at both ends, instants compared whatever the UTC offset): a fill exactly at approved_at is
    INV-06-BEFORE, exactly at expires_at INV-06-EXPIRED. open_window=False is the frozen 1.1.3-1.1.5 closed window
    (approved_at <= filled_at <= expires_at), used by check_record for records labelled 1.1.3, 1.1.4 or 1.1.5."""
    if ap is None:
        return []          # unresolved approval_id: the spec's INV-04 reports it
    pid, aid, out = p.get("position_id", "?"), ap.get("approval_id", "?"), []
    if not isinstance(p.get("fills"), list):
        out.append(f"INV-06-FILLS {pid} fills is a {type(p.get('fills')).__name__}, not an array (fail closed)")
    opens = [f for f in _dicts(p.get("fills")) if str(f.get("action", "")).endswith("_open")]
    for f in opens:
        out += _time_errors("INV-06", f.get("fill_id", "?"), (("filled_at", f.get("filled_at")),))
    if ap.get("status") != "active":
        changed = _ts(ap.get("status_changed_at"))
        if changed is None:
            out.append(f"INV-06-STATUS-UNDATED {pid} approval {aid} status {ap.get('status')!r} without status_changed_at")
        else:
            for f in opens:
                ft = _ts(f.get("filled_at"))
                if ft is None or changed <= ft:
                    out.append(f"INV-06-STATUS {f.get('fill_id', '?')} filled at {_ny(f.get('filled_at'))} but approval "
                               f"{aid} became {ap.get('status')!r} at {_ny(ap.get('status_changed_at'))}")
    if p.get("ticket_id") != ap.get("ticket_id"):
        out.append(f"INV-06-TICKET {pid} is for ticket {p.get('ticket_id')} but approval {aid} is for {ap.get('ticket_id')}")
    if tk is not None and _terms_differ(ap, tk):
        out.append(f"INV-06-TERMS {pid} approval {aid} legs/qty/limit differ from ticket {tk.get('ticket_id')}")
    if rv is not None and review_is_veto(rv):
        out.append(f"INV-06-VETO {pid} approval {aid} approves VETO review {rv.get('review_id')}")
    t0, t1 = _ts(ap.get("approved_at")), _ts(ap.get("expires_at"))
    for f in opens:
        ft = _ts(f.get("filled_at"))
        if open_window:     # spec 1.1.6 wording, so merge() dedupes it against the validator's string
            if ft is None or t0 is None or ft <= t0:
                out.append(f"INV-06-BEFORE {f.get('fill_id', '?')} filled at {_ny(f.get('filled_at'))} not after approval "
                           f"{aid} at {_ny(ap.get('approved_at'))} (valid only while approved_at < filled_at < expires_at)")
            elif t1 is None or ft >= t1:
                out.append(f"INV-06-EXPIRED {f.get('fill_id', '?')} filled at {_ny(f.get('filled_at'))} at or after approval "
                           f"{aid} expiry {_ny(ap.get('expires_at'))} (valid only while approved_at < filled_at < expires_at)")
        elif ft is None or t0 is None or ft < t0:
            out.append(f"INV-06-BEFORE {f.get('fill_id', '?')} filled at {_ny(f.get('filled_at'))} before approval "
                       f"{aid} at {_ny(ap.get('approved_at'))}")
        elif t1 is None or ft > t1:
            out.append(f"INV-06-EXPIRED {f.get('fill_id', '?')} filled at {_ny(f.get('filled_at'))} after approval "
                       f"{aid} expiry {_ny(ap.get('expires_at'))}")
    return out


def reuse_errors(positions: Iterable[dict]) -> list[str]:
    """INV-17-REUSE (spec 1.1.6, SOL-ACCEPT-001 G6): one approval opens at most one paper position. Two or more
    positions referencing the same approval_id -> one error per approval, whatever the approval's status (a consumed
    approval cannot open a second position). Spec wording, so merge() dedupes it against the validator's string."""
    by_ap: dict = {}
    for p in positions:
        if p.get("approval_id") is not None:
            by_ap.setdefault(_key(p.get("approval_id")), []).append(p.get("position_id"))
    return [f"INV-17-REUSE approval {aid} is referenced by {len(pids)} paper positions {pids}; one approval opens at most "
            f"one position (the first is {pids[0]})" for aid, pids in by_ap.items() if len(pids) > 1]


GUARD_CRASHED = "GUARD-CRASHED"


def _key(v: Any) -> Any:
    """A hashable lookup key for an id field (a list / object id is schema-invalid; it never matches a real id)."""
    return v if isinstance(v, (str, int, float, bool, type(None))) else f"<{type(v).__name__}>{v!r}"


def _check_record(rec: dict) -> list[str]:
    errs: list[str] = []
    for key in ("risk_reviews", "tickets", "approvals", "paper_positions"):
        v = rec.get(key)
        if v is not None and not isinstance(v, list):
            errs.append(f"GUARD-TYPE {key} is a {type(v).__name__}, not an array (fail closed)")
        elif isinstance(v, list) and len(_dicts(v)) != len(v):
            errs.append(f"GUARD-TYPE {key} has {len(v) - len(_dicts(v))} entr(ies) that are not objects (fail closed)")
    rvs, tks, aps, pos = (_dicts(rec.get(k)) for k in ("risk_reviews", "tickets", "approvals", "paper_positions"))
    reviews = {_key(r.get("review_id")): r for r in rvs}
    tickets = {_key(t.get("ticket_id")): t for t in tks}
    approvals = {_key(a.get("approval_id")): a for a in aps}
    for rv in rvs:
        errs += review_errors(rv)
    for a in aps:
        errs += approval_errors(a, reviews.get(_key(a.get("review_id"))), tickets.get(_key(a.get("ticket_id"))))
    for p in pos:
        ap = approvals.get(_key(p.get("approval_id")))
        errs += position_errors(p, ap, tickets.get(_key(p.get("ticket_id"))), reviews.get(_key((ap or {}).get("review_id"))),
                                open_window=open_window(rec))
    if open_window(rec):    # spec 1.1.6 rules (1.1.3-1.1.5 records keep their own: no INV-17, closed window)
        errs += reuse_errors(pos)
    return errs


def check_record(rec: Any) -> list[str]:
    """All optpipe-side guard errors for a session record (any 1.x schema_version). [] when clean.

    Never raises (SOL-ACCEPT-001 G5): wrong types and naive / unparsable times are errors. Should a guard still
    crash, the crash is returned as a `GUARD-CRASHED ...` error (fail closed); callers exit 2 on it (guard_crashed)."""
    if not isinstance(rec, dict):
        return [f"GUARD-TYPE the record is a {type(rec).__name__}, not an object (fail closed)"]
    try:
        return _check_record(rec)
    except Exception as e:  # noqa: BLE001 - a guard crash must never pass a record or kill the caller
        return [f"{GUARD_CRASHED} check_record raised {type(e).__name__}: {e} (fail closed)"]


def guard_crashed(errs: Iterable[str]) -> bool:
    """True when the errors include a guard crash (callers exit 2, like a validator crash)."""
    return any(str(e).startswith(GUARD_CRASHED) for e in errs)


def merge(*lists: Iterable[str]) -> list[str]:
    """Concatenate error lists, dropping exact duplicates (a 1.1.3+ record gets the same strings from both)."""
    return list(dict.fromkeys(e for lst in lists for e in lst))
