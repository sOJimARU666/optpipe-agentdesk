"""Idea log transitions after risk review. Owner: Doodle (states) / Poodle (code).

proposed -> vetoed (risk_officer) | approved_pending (risk_officer: not vetoed, awaiting Eugene).
NO TRADE becomes approved_pending (the current recommendation) when every ticket is vetoed.
Nothing here can produce `approved`/`simulated`/`executed`: those need a human approval record.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..records.fields import tspair


def apply_reviews(idea_log: list[dict], reviews: list[dict], at: datetime) -> tuple[list[dict], str]:
    by_ticket: dict[str, list[dict]] = {}
    for r in reviews:
        by_ticket.setdefault(r["ticket_id"], []).append(r)
    when = tspair(at)
    open_ids: list[str] = []
    for idea in idea_log:
        tid = idea.get("ticket_id")
        for r in by_ticket.get(tid or "", []):
            o = r["overall"]
            if o["verdict"] == "VETO":
                st, why = "vetoed", o.get("summary") or ", ".join(o["blocking_rule_ids"])
            else:
                st = "approved_pending"
                why = f"{o['verdict']}: not vetoed; needs Eugene's contract-level approval" + (
                    f" (acknowledge flags {', '.join(o['flag_rule_ids'])})" if o["flag_rule_ids"] else "")
                open_ids.append(tid)
            idea["history"].append({"status": st, "at": when, "actor": "risk_officer", "reason": why[:500],
                                    "ref_ids": [r["review_id"]]})
            idea["current_status"] = st
    nt = next((i for i in idea_log if i["idea_id"] == "idea-no-trade"), None)
    if not open_ids:
        rec = "NO TRADE - every ticket was vetoed by the Risk Officer (or none could be built)"
        if nt is not None:
            nt["history"].append({"status": "approved_pending", "at": when, "actor": "decision_maker",
                                  "reason": "Current recommendation; awaiting Eugene"})
            nt["current_status"] = "approved_pending"
    else:
        rec = (f"{len(open_ids)} HYPOTHETICAL ticket(s) not vetoed, pending Eugene's approval: {', '.join(open_ids)}. "
               "NO TRADE remains available.")
    return idea_log, rec


def quality_index(sess: dict[str, Any]) -> dict[str, list[str]]:
    """Same walk as spec/validate_session.quality_index: every Field with status != ok, by JSON path."""
    out: dict[str, list[str]] = {"missing": [], "stale": [], "conflicting": []}

    def walk(o, path=""):
        if path.startswith("/data_quality") or path.startswith("/risk_reviews"):
            return
        if isinstance(o, dict):
            if {"value", "source_id", "as_of", "status"} <= o.keys() and o["status"] != "ok":
                out[o["status"]].append(path)
            for k, v in o.items():
                walk(v, f"{path}/{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}/{i}")
    walk(sess)
    return out
