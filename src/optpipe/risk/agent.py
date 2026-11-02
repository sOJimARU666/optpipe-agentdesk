"""Risk Officer: reviews every HYPOTHETICAL ticket of a run against Doodle's risk_rules.yaml.

Owner: Doodle (rules, thresholds) / Poodle (code). The rule logic itself is the pure function
`risk.engine.evaluate` (a port of spec/risk_reference.py, parity-tested on all YAML cases). This agent
only builds point-in-time inputs from the draft record and wraps results as schema-v1 risk_reviews.
Approval is always required, whatever the verdict; the Risk Officer can never approve or execute.
"""
from __future__ import annotations

from typing import Any, Optional

from ..agent import Agent
from ..models.contracts import RiskInput, RiskOutput
from ..records.fields import tspair
from ..records.guards import effective_verdict
from .adapter import review_input_from_session
from .engine import evaluate
from .ruleset import Ruleset


def review_id(run_id: str, ticket_id: str) -> str:
    return f"rv-{run_id.removeprefix('run-')}-{ticket_id.removeprefix('tkt-')}"


class RiskOfficerAgent(Agent[RiskInput, RiskOutput]):
    stage = "risk_officer"
    owner = "Doodle (rules) / Poodle (code)"
    input_model = RiskInput
    output_model = RiskOutput

    def __init__(self, ruleset: Ruleset, config_overrides: Optional[dict] = None):
        self.ruleset, self.overrides = ruleset, config_overrides or {}

    def run(self, inp: RiskInput) -> RiskOutput:
        rec = inp.record.model_dump(mode="json", exclude_none=False, exclude_unset=True)
        at = inp.evaluated_at.isoformat()
        reviews = []
        for t in rec.get("tickets", []):
            ri = review_input_from_session(rec, t["ticket_id"], inp.run_id, at)
            ev = evaluate(ri, self.ruleset, self.overrides or None)
            o = ev.overall
            results = []
            for r in ev.results:      # measured/threshold may legitimately be null; optional keys only if set
                d = r.model_dump(mode="json")
                results.append({k: v for k, v in d.items() if v is not None or k in ("measured", "threshold")})
            reasons = {r["rule_id"]: r["reason"] for r in results}
            parts = [f"{rid}: {reasons[rid]}" for rid in o.blocking_rule_ids]
            summary = (f"{o.verdict} - " + "; ".join(parts) if parts else
                       f"{o.verdict} - no blocking rules" + (f"; flags {', '.join(o.flag_rule_ids)}" if o.flag_rule_ids else ""))
            rv: dict[str, Any] = {
                "review_id": review_id(inp.run_id, t["ticket_id"]), "ticket_id": t["ticket_id"], "run_id": inp.run_id,
                "evaluated_at": tspair(inp.evaluated_at), "ruleset_version": self.ruleset.ruleset_version,
                "market_open_at_evaluation": ev.market_open_at_evaluation, "results": results,
                "overall": {"verdict": o.verdict, "blocking_rule_ids": o.blocking_rule_ids,
                            "flag_rule_ids": o.flag_rule_ids, "approval_required": True, "summary": summary[:600]}}
            if self.overrides:
                rv["config_overrides"] = self.overrides
            reviews.append(rv)
        return RiskOutput(risk_reviews=reviews)  # type: ignore[arg-type]

    def summarize_input(self, inp: RiskInput) -> dict[str, Any]:
        return {"tickets": [t["ticket_id"] for t in inp.record.model_dump(mode="json")["tickets"]],
                "run_id": inp.run_id, "evaluated_at": inp.evaluated_at.isoformat(),
                "ruleset_version": self.ruleset.ruleset_version, "ruleset_source": self.ruleset.source}

    def summarize_output(self, out: RiskOutput) -> dict[str, Any]:
        d = out.model_dump(mode="json")["risk_reviews"]
        return {r["ticket_id"]: {"verdict": effective_verdict(r), "blocking": r["overall"]["blocking_rule_ids"],
                                 "flags": r["overall"]["flag_rule_ids"]} for r in d}

    def verdict(self, out: RiskOutput) -> Optional[str]:
        d = out.model_dump(mode="json")["risk_reviews"]
        ok = [r["ticket_id"] for r in d if effective_verdict(r) != "VETO"]
        return (f"{len(ok)} ticket(s) not vetoed (still need Eugene's approval): {', '.join(ok)}" if ok
                else "every ticket VETO -> NO TRADE")
