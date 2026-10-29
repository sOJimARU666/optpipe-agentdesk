"""Demo-scenario labelling. Owner: Moodle (scenario) / Poodle (code).

A fictional scenario writes one canonical session note starting with TAG; every view (CLI summary,
Markdown, dashboard banner) derives its "FICTIONAL DEMO" tag from that note, so a record carries its own
label wherever it is opened.
"""
from __future__ import annotations

from typing import Any, Optional

TAG = "FICTIONAL DEMO"


def scenario_note(sc: Any, broker: str, style: str) -> Optional[str]:
    if sc is None or not sc.fictional:
        return None
    parts = [f"{TAG} scenario '{sc.name}': broker '{broker}', trade style and watchlist are fictional"]
    if sc.account_size_usd is not None:
        parts.append(f"fictional paper account ${sc.account_size_usd:,.0f}")
    if sc.max_loss_per_idea_usd is not None:
        parts.append(f"max risk per idea {sc.max_risk_per_idea_pct:g}% = ${sc.max_loss_per_idea_usd:,.0f}")
    if sc.risk_profile:
        parts.append(f"risk profile '{sc.risk_profile}'")
    if sc.repo_name:
        parts.append(f"proposed repo name {sc.repo_name} (proposal only, not created)")
    return "; ".join(parts)


def tag_for_record(rec: Optional[dict]) -> Optional[str]:
    notes = ((rec or {}).get("session") or {}).get("notes") or []
    return TAG if any(isinstance(n, str) and n.startswith(TAG) for n in notes) else None


def tag_for_settings(settings: Any) -> Optional[str]:
    sc = getattr(settings, "scenario", None)
    return TAG if sc is not None and sc.fictional else None
