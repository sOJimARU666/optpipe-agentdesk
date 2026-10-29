"""Build a ReviewInput from a schema-v1 session record with point-in-time visibility
(a Field is visible only if its source.fetched_at <= evaluated_at). Port of risk_reference.inputs_from_session."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from .review_input import ReviewInput

_MISSING = {"value": None, "status": "missing", "as_of": None}


def review_input_from_session(sess: BaseModel | dict, ticket_id: str, run_id: str, evaluated_at: str) -> ReviewInput:
    s: dict[str, Any] = sess.model_dump(mode="json", exclude_unset=True) if isinstance(sess, BaseModel) else sess
    if s.get("schema_version") != "1.0.0":      # v1.1 superset -> the v1 view the ruleset is defined on
        from ..records.compat import to_v1
        s = to_v1(s)
    t = datetime.fromisoformat(evaluated_at)
    fetched = {x["id"]: datetime.fromisoformat(x["fetched_at"]) for x in s["sources"]}

    def vis(f):
        if not isinstance(f, dict):
            return dict(_MISSING)
        sid = f.get("source_id")
        if sid and sid in fetched and fetched[sid] > t:
            return dict(_MISSING)
        return {"value": f["value"], "status": f["status"], "as_of": f["as_of"]}

    tk = next(x for x in s["tickets"] if x["ticket_id"] == ticket_id)
    cand = next(x for x in s["candidates"] if x["candidate_id"] == tk["candidate_id"])
    snap = next(x for x in cand["snapshots"] if x["run_id"] == run_id)
    contracts = {k["contract_id"]: k for sl in snap["option_chain_slices"] for k in sl["contracts"]}
    legs = []
    for l in tk["legs"]:
        k = contracts.get(l["contract_id"], {})
        legs.append({**{x: l[x] for x in ("leg_id", "type", "side", "strike", "expiry", "ratio")},
                     "bid": vis(k.get("bid")), "ask": vis(k.get("ask")), "open_interest": vis(k.get("open_interest"))})
    events = []
    for e in s["event_calendar"]["events"]:
        ev = {"event_id": e["event_id"], "kind": e["kind"], "underlying": e["underlying"], "timing": e["timing"],
              "scheduled_at": vis(e["scheduled_at"])}
        if "dividend_amount" in e:
            ev["dividend_amount"] = vis(e["dividend_amount"])
        events.append(ev)
    coverage = [{"kind": c["kind"], "underlying": c["underlying"], "checked_through": vis(c["checked_through"])}
                for c in s["event_calendar"]["coverage"]]
    iv = snap.get("iv") or {}
    tickets = {x["ticket_id"]: x for x in s["tickets"]}
    open_pos = [{"underlying": tickets[p["ticket_id"]]["underlying"], "max_loss_usd": tickets[p["ticket_id"]]["max_loss"]["amount_usd"]}
                for p in s["paper_positions"] if p["status"] == "open" and p["ticket_id"] != ticket_id]
    return ReviewInput.model_validate({
        "evaluated_at": evaluated_at,
        "underlying": {"symbol": cand["underlying"], "last": vis(snap["quote"]["last"])},
        "ticket": {"underlying": tk["underlying"], "quantity": tk["quantity"], "limit_price": tk["limit_price"],
                   "hold_window_start": tk["hold_window"]["start"]["ny"], "hold_window_end": tk["hold_window"]["end"]["ny"],
                   "intended_event_ids": tk["intended_event_ids"]},
        "legs": legs, "events": events, "coverage": coverage,
        "iv": {"iv30_current": vis(iv.get("iv30_current")), "iv30_history": vis(iv.get("iv30_history"))},
        "open_positions": {"value": open_pos, "status": "ok", "as_of": evaluated_at},
    })
