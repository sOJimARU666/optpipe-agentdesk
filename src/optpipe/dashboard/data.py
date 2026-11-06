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

from ..records.compat import V11_SHAPE, from_v1

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
    session_ref_ok: Optional[bool] = None     # trace.session_ref.sha256 matches the record content (minus trace_ref)
    watch_sidecar_ok: Optional[bool] = None   # <session>.watch.json.sha256 matches the watch file
    watch_session_ok: Optional[bool] = None   # watch.session.sha256 matches session-<date>.json
    guard_errors: list[str] = field(default_factory=list)   # records/guards.check_record on the record (SOL-ACCEPT-001 G1)
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
    watch: Optional[dict[str, Any]] = None    # <session>.watch.json (M3 watcher output), if present

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


def guard_errors(rec: Any) -> list[str]:
    """optpipe's record guards (INV-05/06/16) on a record the dashboard is about to show. Never raises: a guard
    crash is itself an error (fail closed), so the page still renders and says the record is not trustworthy."""
    from ..records.guards import check_record
    if not isinstance(rec, dict):
        return [f"GUARD-CRASHED record is a {type(rec).__name__}, not an object (fail closed)"]
    try:
        return list(check_record(rec))
    except Exception as e:  # noqa: BLE001 - display path: report, never crash
        return [f"GUARD-CRASHED check_record raised {type(e).__name__}: {e} (fail closed)"]


class OutDir:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _session_path(self, date: str, suffix: str) -> Path:
        root = self.root.resolve()
        path = (root / f"session-{date}{suffix}").resolve()
        if not path.is_relative_to(root):
            raise KeyError(date)
        return path

    def dates(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted((m.group(1) for p in self.root.iterdir() if (m := _V1.match(p.name))), reverse=True)

    def load(self, date: str) -> Session:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise KeyError(date)
        jp = self._session_path(date, ".json")
        vp = self._session_path(date, ".v1_1.json")
        tp = self._session_path(date, ".trace.json")
        sp = self._session_path(date, ".trace.json.sha256")
        if not jp.exists():
            raise KeyError(date)
        main = json.loads(jp.read_text())
        if main.get("schema_version") in V11_SHAPE:
            rec, origin = main, f"record (schema {main['schema_version']})"
        elif vp.exists():
            rec, origin = json.loads(vp.read_text()), "v1.1 preview (older output)"
        else:
            rec, origin = from_v1(json.loads(jp.read_text())), "v1 (upgraded for display)"
        integ, trace = Integrity(), None
        integ.guard_errors = guard_errors(rec)
        integ.problems += [f"guard: {e}" for e in integ.guard_errors]
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
                    integ.problems.append("trace_ref in the record does not match the trace file")
            from ..records.store import verify_session_ref
            ok, msg = verify_session_ref(jp, trace)
            integ.session_ref_ok = ok
            if ok is False:
                integ.problems.append(msg)
        else:
            integ.problems.append(f"{tp.name} not found")
        wp = self._session_path(date, ".watch.json")
        wsp = self._session_path(date, ".watch.json.sha256")
        watch = None
        if wp.exists():
            wb = wp.read_bytes()
            watch = json.loads(wb)
            if wsp.exists():
                integ.watch_sidecar_ok = wsp.read_text().split()[0] == sha256_bytes(wb)
                if not integ.watch_sidecar_ok:
                    integ.problems.append(f"{wsp.name} does not match {wp.name}")
            wref = (watch.get("session") or {}).get("sha256")
            if wref:
                integ.watch_session_ok = wref == sha256_bytes(jp.read_bytes())
                if not integ.watch_session_ok:
                    integ.problems.append(f"{wp.name} was made from a different {jp.name} (re-run optpipe watch)")
        files = {k: p.name for k, p in (("v1", jp), ("v1_1", vp), ("trace", tp), ("sha256", sp),
                                        ("md", self._session_path(date, ".md")), ("watch", wp),
                                        ("watch_sha256", wsp)) if p.exists()}
        return Session(date, rec, origin, trace, integ, files, watch)


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
