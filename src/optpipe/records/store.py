"""Persistence. Owner: Doodle (schema) / Poodle (code). Writes only to the local output directory.

  session-<date>.json          canonical record, strict schema v1 (Doodle's locked spec)  <- validate_session.py
  session-<date>.trace.json    hand-off trace; session_ref = {path, sha256} of the v1 record
  session-<date>.trace.json.sha256   `sha256sum` line for the trace file
  session-<date>.v1_1.json     PREVIEW of the v1.1 proposal (spec_patches/v1.1): per-run scans/snapshots,
                               phase "closed", raw tick values, trace_ref {path, sha256}; used for --append
  session-<date>.md            rendered summary
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..models.legacy import LegacySessionRecord
from ..models.schema_v1 import SessionRecordV1
from ..models.schema_v1_1 import SessionRecordV11
from ..models.session import FileRef, SessionTrace
from .compat import from_v1, strip_trace_ref, to_v1
from .markdown import render_markdown


@dataclass
class Written:
    json: Path
    md: Path
    trace: Path
    v1_1: Path
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
    v1 = to_v1(rec)
    SessionRecordV1.model_validate(v1)                       # never write an invalid record
    jp, mp, tp = paths(out_dir, rec["session"]["date"])
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(_dump(v1))
    jsha = sha256_file(jp)
    trace.session_ref = FileRef(path=jp.name, sha256=jsha, schema_version=v1["schema_version"])
    tp.write_text(trace.model_dump_json(indent=2) + "\n")
    tsha = sha256_file(tp)
    tp.with_name(tp.name + ".sha256").write_text(f"{tsha}  {tp.name}\n")
    v11 = {**from_v1(rec), "trace_ref": {"path": tp.name, "sha256": tsha}}
    SessionRecordV11.model_validate(v11)
    vp = v1_1_path(jp)
    vp.write_text(_dump(v11))
    mp.write_text(render_markdown(v11, trace))
    return Written(jp, mp, tp, vp, jsha, tsha)


def read_session(path: str | Path) -> dict:
    d = json.loads(Path(path).read_text())
    (SessionRecordV11 if d.get("schema_version") == "1.1.0" else SessionRecordV1).model_validate(d)
    return d


def read_session_for_append(json_path: str | Path) -> dict:
    """Prefer the v1.1 preview (keeps per-run history); fall back to upgrading the v1 record."""
    jp = Path(json_path)
    vp = v1_1_path(jp)
    return strip_trace_ref(read_session(vp)) if vp.exists() else from_v1(read_session(jp))


def read_trace(path: str | Path) -> SessionTrace | None:
    p = Path(path)
    return SessionTrace.model_validate_json(p.read_text()) if p.exists() else None


def read_legacy_session(path: str | Path) -> LegacySessionRecord:
    return LegacySessionRecord.model_validate(json.loads(Path(path).read_text()))
