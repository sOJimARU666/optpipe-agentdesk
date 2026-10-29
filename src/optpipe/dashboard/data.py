"""Load sessions from an out/ directory for the dashboard (read-only)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from ..records.compat import from_v1

BERLIN = ZoneInfo("Europe/Berlin")
NY = ZoneInfo("America/New_York")
_V1 = re.compile(r"^session-(\d{4}-\d{2}-\d{2})\.json$")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass
class Integrity:
    trace_sha256: Optional[str] = None
    sidecar_ok: Optional[bool] = None         # trace.json.sha256 matches the trace file
    trace_ref_ok: Optional[bool] = None       # v1.1 trace_ref.sha256 matches the trace file
    session_ref_ok: Optional[bool] = None     # trace.session_ref.sha256 matches session-<date>.json
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and self.trace_sha256 is not None


@dataclass
class Session:
    date: str
    record: dict[str, Any]                    # v1.1 shape (preview file, or upgraded from v1)
    origin: str                               # "v1.1 preview" | "v1 (upgraded for display)"
    trace: Optional[dict[str, Any]]
    integrity: Integrity
    files: dict[str, str]

    @property
    def runs(self) -> list[dict]:
        return self.record.get("runs", [])

    def trace_run(self, run_id: str) -> Optional[dict]:
        return next((r for r in (self.trace or {}).get("runs", []) if r["run_id"] == run_id), None)

    def per_run(self, key: str, run_id: Optional[str]) -> Optional[dict]:
        """premarket_scans / global_snapshots entry for run_id (latest when None)."""
        items = self.record.get(key) or []
        if not items:
            legacy = self.record.get(key[:-1])
            return legacy if legacy and (run_id is None or legacy.get("run_id") == run_id) else None
        if run_id is None:
            return items[-1]
        return next((i for i in items if i.get("run_id") == run_id), None)


class OutDir:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def dates(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted((m.group(1) for p in self.root.iterdir() if (m := _V1.match(p.name))), reverse=True)

    def load(self, date: str) -> Session:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise KeyError(date)
        jp = self.root / f"session-{date}.json"
        vp = self.root / f"session-{date}.v1_1.json"
        tp = self.root / f"session-{date}.trace.json"
        sp = tp.with_name(tp.name + ".sha256")
        if not jp.exists():
            raise KeyError(date)
        if vp.exists():
            rec, origin = json.loads(vp.read_text()), "v1.1 preview"
        else:
            rec, origin = from_v1(json.loads(jp.read_text())), "v1 (upgraded for display)"
        integ, trace = Integrity(), None
        if tp.exists():
            tb = tp.read_bytes()
            trace = json.loads(tb)
            integ.trace_sha256 = sha256_bytes(tb)
            if sp.exists():
                integ.sidecar_ok = sp.read_text().split()[0] == integ.trace_sha256
                if not integ.sidecar_ok:
                    integ.problems.append(f"{sp.name} does not match {tp.name}")
            ref = rec.get("trace_ref")
            if ref:
                integ.trace_ref_ok = ref.get("sha256") == integ.trace_sha256 and ref.get("path") == tp.name
                if not integ.trace_ref_ok:
                    integ.problems.append("trace_ref in the v1.1 preview does not match the trace file")
            sref = trace.get("session_ref")
            if sref:
                integ.session_ref_ok = sref.get("sha256") == sha256_bytes(jp.read_bytes())
                if not integ.session_ref_ok:
                    integ.problems.append(f"trace.session_ref does not match {jp.name}")
        else:
            integ.problems.append(f"{tp.name} not found")
        files = {k: p.name for k, p in (("v1", jp), ("v1_1", vp), ("trace", tp), ("sha256", sp),
                                        ("md", self.root / f"session-{date}.md")) if p.exists()}
        return Session(date, rec, origin, trace, integ, files)


# ---------------------------------------------------------------- formatting helpers (Jinja filters)
def berlin(ts: Optional[str]) -> str:
    """Any ISO timestamp (Z, offset) -> 'YYYY-MM-DD HH:MM Berlin'."""
    if not ts:
        return "-"
    if isinstance(ts, dict):
        return pair(ts)
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if d.tzinfo is None:
        return ts
    return d.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M %Z")


def clock(ts: Optional[str]) -> str:
    """Wall-clock trace stamp -> 'HH:MM:SS.mmm Berlin'."""
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(BERLIN)
    except ValueError:
        return str(ts)
    return f"{d:%H:%M:%S}.{d.microsecond // 1000:03d} {d:%Z}"


def pair(p: Any) -> str:
    """{ny, berlin} pair -> '07:52 ET (13:52 Berlin)'."""
    if not isinstance(p, dict) or "ny" not in p:
        return berlin(p) if isinstance(p, str) else "-"
    ny = datetime.fromisoformat(p["ny"])
    b = datetime.fromisoformat(p["berlin"]) if p.get("berlin") else ny.astimezone(BERLIN)
    return f"{ny:%Y-%m-%d %H:%M} ET ({b:%H:%M} Berlin)"


def fval(f: Any, digits: int = 4) -> str:
    """Field object -> display value."""
    if not isinstance(f, dict) or "status" not in f:
        return "-" if f is None else str(f)
    v = f.get("value")
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.{digits}f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:,.0f}"
    if isinstance(v, list):
        return f"[{len(v)} values]"
    return str(v)


def ftitle(f: Any) -> str:
    """Tooltip: status, source, as_of, method/formula/note."""
    if not isinstance(f, dict) or "status" not in f:
        return ""
    bits = [f"status: {f['status']}", f"source: {f.get('source_id') or '-'}", f"as_of: {f.get('as_of') or '-'}"]
    for k in ("method", "formula", "note"):
        if f.get(k):
            bits.append(f"{k}: {f[k]}")
    return " | ".join(bits)


def compact(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(", ", ": "), default=str)
    return str(v)
