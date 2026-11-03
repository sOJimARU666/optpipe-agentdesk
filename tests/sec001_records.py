"""Record builders for the FIX-SEC-001 tests (AUDIT-001B P0): a schema-valid FLAG review with an approval and
an open paper position, built from Doodle's session.example.json. PAPER ONLY, synthetic data, no network."""
from __future__ import annotations

import copy

FLAGS = ["R08_MACRO_EVENTS", "R10_IV_VS_HISTORY"]


def ts(ny: str) -> dict:
    """TsPair on 2026-10-13 (NY EDT -04:00, Berlin CEST +02:00) from an NY HH:MM."""
    h, m = map(int, ny.split(":"))
    return {"ny": f"2026-10-13T{h:02d}:{m:02d}:00-04:00", "berlin": f"2026-10-13T{h + 6:02d}:{m:02d}:00+02:00"}


def flag_review(rec: dict) -> dict:
    """Turn the example's last review into an honest FLAG review: hard rules PASS, the two soft rules stay
    UNKNOWN (so they are the flags). Returns the review."""
    rv = rec["risk_reviews"][-1]
    for x in rv["results"]:
        if x["severity"] == "hard":
            x["result"] = "PASS"
    rv["overall"].update(verdict="FLAG", blocking_rule_ids=[], flag_rule_ids=list(FLAGS), approval_required=True)
    return rv


def approved(example: dict, acknowledged=FLAGS, status: str = "active", **extra) -> dict:
    """The example session with one FLAG review and an approval of it (acknowledged_flags=acknowledged)."""
    r = copy.deepcopy(example)
    rv = flag_review(r)
    tk = next(t for t in r["tickets"] if t["ticket_id"] == rv["ticket_id"])
    a = {"approval_id": "apr-1", "ticket_id": tk["ticket_id"], "review_id": rv["review_id"],
         "approver": {"name": "Eugene", "channel": "web_ui"}, "approved_at": ts("12:20"), "expires_at": ts("16:00"),
         "legs": copy.deepcopy(tk["legs"]), "quantity": tk["quantity"], "limit_price": copy.deepcopy(tk["limit_price"]),
         "status": status, **extra}
    if acknowledged is not None:
        a["acknowledged_flags"] = list(acknowledged)
    r["approvals"] = [a]
    for i in r["idea_log"]:
        if i.get("ticket_id") == tk["ticket_id"]:
            i["current_status"] = "approved"
            i["history"].append({"status": "approved", "at": ts("12:20"), "actor": "user",
                                 "reason": "paper approval with flags acknowledged", "ref_ids": [a["approval_id"]]})
    return r


def with_position(r: dict, fill_at: str = "12:30", ticket_id: str | None = None) -> dict:
    """Add an open paper position on the approval, opened at fill_at (NY) inside the validity window, at a net
    debit of 3.05 (limit 3.10)."""
    a = r["approvals"][0]
    tk = next(t for t in r["tickets"] if t["ticket_id"] == a["ticket_id"])
    l1, l2 = tk["legs"]
    fill = lambda fid, leg, act, px: {"fill_id": fid, "leg_id": leg["leg_id"], "contract_id": leg["contract_id"],  # noqa: E731
                                      "action": act, "qty": tk["quantity"], "price": px, "filled_at": ts(fill_at),
                                      "quote_source_id": "src-nasdaq-jpm-chain-0752", "simulated": True}
    r["paper_positions"] = [{"position_id": "pos-1", "ticket_id": ticket_id or a["ticket_id"], "approval_id": a["approval_id"],
                             "status": "open", "fill_model": "mid", "marks": [],
                             "fills": [fill("f-1", l1, "buy_to_open", 5.0), fill("f-2", l2, "sell_to_open", 1.95)]}]
    return r
