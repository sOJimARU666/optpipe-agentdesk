"""Europe/Asia index snapshot providers.

No machine-readable live source has been agreed yet (open question for Doodle), so:
- RecordGlobalSnapshot reads `global_snapshot` (+ the sources it cites) from an existing schema-v1
  session record, read-only (offline default: Doodle's spec/session.example.json, curated by hand,
  with per-index sources and conflicts already resolved into conflicting Fields).
- UnavailableGlobalSnapshot returns every configured index as a `missing` Field (live placeholder).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..records.fields import MISS


class RecordGlobalSnapshot:
    def __init__(self, session_json: str | Path):
        self.path = Path(session_json)

    def get_snapshot(self, run_id: str) -> tuple[dict, list[dict]]:
        if not self.path.exists():
            return UnavailableGlobalSnapshot().get_snapshot(run_id)
        rec = json.loads(self.path.read_text())
        g = dict(rec["global_snapshot"], run_id=run_id)
        used: set[str] = set()

        def walk(o):
            if isinstance(o, dict):
                if isinstance(o.get("source_id"), str):
                    used.add(o["source_id"])
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(g)
        sources = [s for s in rec["sources"] if s["id"] in used]
        return g, sources


class UnavailableGlobalSnapshot:
    def __init__(self, indexes: list[str] | None = None):
        self.indexes = indexes or []

    def get_snapshot(self, run_id: str) -> tuple[dict, list[dict]]:
        inst = [{"symbol": s, "region": "other", "level": MISS(note="no global snapshot source configured"),
                 "change_pct": MISS(note="no global snapshot source configured"), "session_state": "unknown"}
                for s in self.indexes]
        return {"run_id": run_id, "instruments": inst, "themes": []}, []
