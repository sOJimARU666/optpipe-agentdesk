"""Persistence. Owner: Doodle (schema) / Poodle (code). Writes only to the local output directory.

  session-<date>.json          canonical record, schema 1.1.0 (Doodle's spec 1.1.0) <- validate_session.py
                               per-run premarket_scans[]/global_snapshots[] + the singular v1 objects,
                               phase "closed", raw tick fields, trace_ref {path, sha256} -> trace (INV-11)
  session-<date>.trace.json    hand-off trace; session_ref = {path, schema_version} (no sha256: the record
                               pins the trace's hash, and a hash in both directions would be a cycle)
  session-<date>.trace.json.sha256   `sha256sum` line for the trace file
  session-<date>.md            rendered summary
Older outputs (v1 session-<date>.json + session-<date>.v1_1.json preview) are still read.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..models.legacy import LegacySessionRecord
from ..models.schema_v1 import SessionRecordV1
from ..models.schema_spec import SessionRecordSpec
from ..models.schema_v1_1 import SessionRecordV11
from ..models.session import FileRef, SessionTrace
from .compat import from_v1, strip_trace_ref, to_v1
from .markdown import render_markdown


@dataclass
class Written:
    json: Path
    md: Path
    trace: Path
    json_sha256: str
    trace_sha256: str

    def __iter__(self):          # backwards compatible: jp, mp, tp = write_session(...)
        return iter((self.json, self.md, self.trace))


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def paths(out_dir: str | Path, date: str) -> tuple[Path, Path, Path]:
    out = Path(out_dir)
    stem = f"session-{date}"
    return out / f"{stem}.json", out / f"{stem}.md", out / f"{stem}.trace.json"


def v1_1_path(json_path: Path) -> Path:
    return json_path.with_name(json_path.name.replace(".json", ".v1_1.json"))


def _dump(o) -> str:
    return json.dumps(o, indent=2, ensure_ascii=False) + "\n"


def write_session(rec: dict, trace: SessionTrace, out_dir: str | Path) -> Written:
    jp, mp, tp = paths(out_dir, rec["session"]["date"])
    jp.parent.mkdir(parents=True, exist_ok=True)
    v11 = strip_trace_ref(from_v1(rec))
    trace.session_ref = FileRef(path=jp.name, sha256=None, schema_version=v11["schema_version"])
    tp.write_text(trace.model_dump_json(indent=2) + "\n")
    tsha = sha256_file(tp)
    tp.with_name(tp.name + ".sha256").write_text(f"{tsha}  {tp.name}\n")
    v11["trace_ref"] = {"path": tp.name, "sha256": tsha}
    SessionRecordV11.model_validate(v11)
    SessionRecordSpec.model_validate(v11)                    # never write a record Doodle's schema rejects
    jp.write_text(_dump(v11))
    vp = v1_1_path(jp)
    if vp.exists():                                          # superseded preview from an older run of ours
        vp.unlink()
    mp.write_text(render_markdown(v11, trace))
    return Written(jp, mp, tp, sha256_file(jp), tsha)


def read_session(path: str | Path) -> dict:
    d = json.loads(Path(path).read_text())
    (SessionRecordSpec if d.get("schema_version") == "1.1.0" else SessionRecordV1).model_validate(d)
    return d


def read_session_for_append(json_path: str | Path) -> dict:
    """1.1.0 record as-is; older outputs: the v1.1 preview if present, else the upgraded v1 record."""
    jp = Path(json_path)
    d = read_session(jp)
    if d.get("schema_version") == "1.1.0":
        return strip_trace_ref(d)
    vp = v1_1_path(jp)
    return strip_trace_ref(read_session(vp)) if vp.exists() else from_v1(d)


def read_trace(path: str | Path) -> SessionTrace | None:
    p = Path(path)
    return SessionTrace.model_validate_json(p.read_text()) if p.exists() else None


def read_legacy_session(path: str | Path) -> LegacySessionRecord:
    return LegacySessionRecord.model_validate(json.loads(Path(path).read_text()))
