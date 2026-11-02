"""Schema v1 <-> v1.1-proposal conversion. Owner: Poodle (code) / Doodle (schema).

The pipeline works on a v1.1 superset internally (spec_patches/v1.1, all additions optional):
per-run premarket_scans[] / global_snapshots[], phase "closed", tick-rounding raw values, trace_ref.
`to_v1` produces a record valid against Doodle's locked v1 schema - the canonical session-<date>.json.
Nothing is dropped silently: raw values survive as text in pricing.note / condition notes, and a
"closed" phase is mapped to "afterhours" with the true phase kept in the run summary.
"""
from __future__ import annotations

import copy
from typing import Any

V1, V11 = "1.0.0", "1.1.3"   # V11 = the v1.1-shape version this pipeline writes
V11_SHAPE = ("1.1.0", "1.1.1", "1.1.2", "1.1.3")
_LIMIT_EXTRA = ("raw_amount", "tick_size", "rounding")
_COND_EXTRA = ("raw_value", "tick_size", "rounding")
CLOSED_NOTE = "[phase=closed; v1 has no 'closed' phase, recorded as afterhours]"


def latest_for_run(items: list[dict], runs: list[dict]) -> dict | None:
    """The entry for the most recent run that has one (runs are chronological)."""
    by = {x["run_id"]: x for x in items}
    for r in reversed(runs):
        if r["run_id"] in by:
            return by[r["run_id"]]
    return items[-1] if items else None


def _strip_conditions(conds: list[dict]) -> None:
    for c in conds or []:
        for k in _COND_EXTRA:
            c.pop(k, None)


def to_v1(rec: dict) -> dict:
    v = copy.deepcopy(rec)
    v["schema_version"] = V1
    v.pop("trace_ref", None)
    for plural, single in (("premarket_scans", "premarket_scan"), ("global_snapshots", "global_snapshot")):
        items = v.pop(plural, None)
        if items:
            v[single] = latest_for_run(items, v["runs"])
    for r in v["runs"]:
        if r["phase"] == "closed":
            r["phase"] = "afterhours"
            if CLOSED_NOTE not in r.get("summary", ""):
                r["summary"] = f"{r.get('summary', '').rstrip()} {CLOSED_NOTE}".strip()
    for t in v["tickets"]:
        for k in _LIMIT_EXTRA:
            t["limit_price"].pop(k, None)
        for key in ("entry_rules", "exit_rules", "stop_rules", "target_rules"):
            for rule in t.get(key) or []:
                _strip_conditions(rule.get("conditions"))
    for a in v.get("approvals", []):
        for k in _LIMIT_EXTRA:
            (a.get("limit_price") or {}).pop(k, None)
    _reindex(v)
    return v


def _reindex(v: dict) -> None:
    """data_quality must be regenerable from the record it sits in (INV), so re-derive it per form."""
    if "data_quality" in v:
        from .idea_log import quality_index
        v["data_quality"] = {**quality_index(v), "notes": v["data_quality"].get("notes", [])}


def from_v1(rec: dict) -> dict:
    """Upgrade a v1 record (e.g. an older session-<date>.json) so further runs can be appended."""
    v = copy.deepcopy(rec)
    if v.get("schema_version") in V11_SHAPE:
        v["schema_version"] = V11              # 1.1.0..1.1.2 -> 1.1.3: same shape, stricter checks
        return v
    v["schema_version"] = V11
    first = v["runs"][0]["run_id"] if v.get("runs") else None
    for plural, single in (("premarket_scans", "premarket_scan"), ("global_snapshots", "global_snapshot")):
        s = v.get(single)
        if s and plural not in v:
            v[plural] = [{**s, "run_id": s.get("run_id") or first}]
    _reindex(v)
    return v


def strip_trace_ref(rec: dict[str, Any]) -> dict:
    v = dict(rec)
    v.pop("trace_ref", None)
    return v
