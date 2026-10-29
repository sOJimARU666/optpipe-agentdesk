"""Planner (decision maker): candidates -> HYPOTHETICAL tickets + idea log, always with a NO TRADE option.

Owner: Doodle (strategy) / Poodle (code). Builds the bias-direction spread per candidate and the
opposite structure as an alternative; it never selects for execution: the Risk Officer reviews every
ticket and Eugene must approve. Ideas that cannot be built are logged as rejected (no ticket).
"""
from __future__ import annotations

from typing import Any

from ..agent import Agent
from ..models.common import Bias
from ..models.contracts import PlannerInput, PlannerOutput
from ..records.fields import slug, tspair
from .builder import BEAR, BULL, Idea, build_vertical

PRIMARY = {Bias.BULLISH: [BULL], Bias.BEARISH: [BEAR], Bias.NEUTRAL: [BULL, BEAR]}
OPPOSITE = {BULL: BEAR, BEAR: BULL}


class PlannerAgent(Agent[PlannerInput, PlannerOutput]):
    stage = "planner"
    owner = "Doodle (strategy) / Poodle (code)"
    input_model = PlannerInput
    output_model = PlannerOutput

    def run(self, inp: PlannerInput) -> PlannerOutput:
        ideas: list[Idea] = []
        for cand in (c.model_dump(mode="json", exclude_none=False) for c in inp.candidates):
            bias = inp.bias.get(cand["candidate_id"], Bias.NEUTRAL)
            thesis = "; ".join(c["value"] for c in cand["catalysts"] if c.get("value"))[:300]
            plan = list(PRIMARY[bias]) + ([OPPOSITE[PRIMARY[bias][0]]] if bias != Bias.NEUTRAL else [])
            for strat in plan:
                ideas.append(build_vertical(cand, strat, inp.params, inp.as_of, inp.run_id, thesis))
        built = [i for i in ideas if i.ticket]
        stale = any(i.ticket["pricing"]["status"] != "ok" for i in built)
        alts = []
        for n, i in enumerate(ideas):
            tid = i.ticket["ticket_id"] if i.ticket else None
            alts.append({"alt_id": f"alt-{slug(i.underlying)}-{'bcs' if i.strategy == BULL else 'bps'}",
                         "kind": "trade", "description": i.description, "ticket_id": tid,
                         "decision": ("rejected" if not tid else "selected" if i is (built[0] if built else None)
                                      else "not_selected"),
                         "reason": i.reason or ("first-ranked buildable idea; still subject to Risk Officer and approval"
                                                if built and i is built[0] else "alternative; subject to Risk Officer")})
        no_trade = {"alt_id": "alt-no-trade", "kind": "no_trade", "description": "NO TRADE", "ticket_id": None,
                    "decision": "preferred" if (stale or not built) else "not_selected",
                    "reason": ("Option quotes are not live (stale/missing); re-quote after the open"
                               if stale else "No buildable idea" if not built else
                               "Always available; becomes the outcome if every ticket is vetoed")}
        alts.append(no_trade)
        for i in built:
            i.ticket["dm_alternatives"] = alts
        at = tspair(inp.as_of)
        log = []
        for i in ideas:
            h = [{"status": "proposed", "at": at, "actor": "decision_maker",
                  "reason": f"{i.description} from candidate {i.candidate_id}"}]
            if not i.ticket:
                h.append({"status": "rejected", "at": at, "actor": "decision_maker", "reason": i.reason})
            log.append({"idea_id": f"idea-{slug(i.underlying)}-{'bcs' if i.strategy == BULL else 'bps'}",
                        "description": i.description, "ticket_id": i.ticket["ticket_id"] if i.ticket else None,
                        "current_status": "proposed" if i.ticket else "rejected", "history": h})
        log.append({"idea_id": "idea-no-trade", "description": "No trade", "ticket_id": None,
                    "current_status": "proposed",
                    "history": [{"status": "proposed", "at": at, "actor": "decision_maker", "reason": no_trade["reason"]}]})
        notes = [f"{i.description}: not built ({i.reason})" for i in ideas if not i.ticket]
        return PlannerOutput(tickets=[i.ticket for i in built], idea_log=log, notes=notes)  # type: ignore[misc]

    def summarize_input(self, inp: PlannerInput) -> dict[str, Any]:
        return {"candidates": [c.underlying for c in inp.candidates],
                "max_risk_usd": inp.params.max_risk_per_trade_usd}

    def summarize_output(self, out: PlannerOutput) -> dict[str, Any]:
        return {"tickets": [t.ticket_id for t in out.tickets],
                "rejected_ideas": [i.idea_id for i in out.idea_log if i.current_status.value == "rejected"]}
