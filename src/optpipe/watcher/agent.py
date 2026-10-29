"""Watcher (STUB): defines inactive watch rules for tickets the Risk Officer did not veto.

Owner: Doodle (what to watch) / Poodle (code). No monitoring loop, no schedule, no market access:
rules stay `active: false` until Eugene approves a ticket (approvals are entered by a human), and the
strongest action is `suggest_close`. `force_close_paper` exists in the schema but is never emitted here.
"""
from __future__ import annotations

from typing import Any

from ..agent import Agent
from ..models.contracts import WatcherInput, WatcherOutput
from ..records.fields import slug


class WatcherAgent(Agent[WatcherInput, WatcherOutput]):
    stage = "watcher"
    owner = "Doodle (rules) / Poodle (code)"
    input_model = WatcherInput
    output_model = WatcherOutput

    def run(self, inp: WatcherInput) -> WatcherOutput:
        d = inp.model_dump(mode="json")
        vetoed = {r["ticket_id"] for r in d["risk_reviews"] if r["overall"]["verdict"] == "VETO"}
        rules = []
        for t in d["tickets"]:
            if t["ticket_id"] in vetoed:
                continue
            tid = t["ticket_id"]
            w = lambda kind: f"w-{slug(tid.removeprefix('tkt-'))}-{kind}"
            tgt = {"ticket_id": tid, "position_id": None}
            conds = lambda key, metric: [c for r in t[key] for c in r["conditions"] if c["metric"] == metric]  # noqa: E731
            stop = next((c["value"] for c in conds("stop_rules", "position_value")), None)
            target = next((c["value"] for c in conds("target_rules", "position_value")), None)
            u_stop = next(iter(conds("stop_rules", "underlying_close")), None)
            u_tgt = next(iter(conds("target_rules", "underlying_last")), None)
            base = {"target": tgt, "active": False, "schedule": None}
            if stop is not None and target is not None:
                rules.append({**base, "watch_id": w("value"), "kind": "option_value", "severity": "action",
                              "action": "suggest_close", "note": f"stop {stop} / target {target}",
                              "conditions": [{"metric": "position_value", "op": "lte", "value": stop},
                                             {"metric": "position_value", "op": "gte", "value": target}]})
            if u_stop is not None and u_tgt is not None:
                rules.append({**base, "watch_id": w("under"), "kind": "underlying_price", "severity": "warn",
                              "action": "alert", "note": f"{t['underlying']} through {u_stop['value']} or {u_tgt['value']}",
                              "conditions": [{"metric": "underlying_last", "op": u_stop["op"], "value": u_stop["value"]},
                                             {"metric": "underlying_last", "op": u_tgt["op"], "value": u_tgt["value"]}]})
            rules.append({**base, "watch_id": w("dte"), "kind": "dte", "severity": "action", "action": "suggest_close",
                          "note": "time stop", "conditions": [
                              {"metric": "date_ny", "op": "gte", "value": t["hold_window"]["end"]["ny"][:10]}]})
            if any(leg["side"] == "sell" for leg in t["legs"]):
                rules.append({**base, "watch_id": w("assign"), "kind": "assignment", "severity": "warn",
                              "action": "alert", "note": "short leg ITM or ex-dividend in window",
                              "conditions": [{"metric": "short_leg_itm", "op": "eq", "value": True},
                                             {"metric": "ex_dividend_in_window", "op": "eq", "value": True}]})
        return WatcherOutput(watcher_rules=rules)  # type: ignore[arg-type]

    def summarize_input(self, inp: WatcherInput) -> dict[str, Any]:
        return {"tickets": [t["ticket_id"] for t in inp.model_dump(mode="json")["tickets"]],
                "reviews": len(inp.risk_reviews)}

    def summarize_output(self, out: WatcherOutput) -> dict[str, Any]:
        return {"rules": [w["watch_id"] for w in out.model_dump(mode="json")["watcher_rules"]], "note": out.note}
