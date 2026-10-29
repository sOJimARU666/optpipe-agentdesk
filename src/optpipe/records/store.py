"""Persistence. Owner: Doodle (schema) / Poodle (code). Writes only to the local output directory.

  session-<date>.json          canonical record, schema 1.1.1 (Doodle's spec 1.1.1) <- validate_session.py
                               per-run premarket_scans[]/global_snapshots[] + the singular v1 objects,
                               phase "closed", raw tick fields, trace_ref {path, sha256} -> trace (INV-11)
  session-<date>.trace.json    hand-off trace; session_ref = {path, sha256, sha256_covers, schema_version}.
                               session_ref.sha256 = sha256 of the record WITHOUT trace_ref in canonical JSON
                               (see session_content_sha256), so there is no hash cycle: the trace pins the
                               record's content, and the record's trace_ref pins the trace file's bytes.
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


SESSION_REF_COVERS = ("canonical-json/1: session record minus top-level trace_ref; "
                      "json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False), UTF-8")


def session_content_sha256(rec: dict) -> str:
    """Hash for trace.session_ref: the record without trace_ref, in canonical JSON (sorted keys, no whitespace,
    UTF-8). Independent of indentation and key order, so it can be recomputed from the written file."""
    body = {k: v for k, v in rec.items() if k != "trace_ref"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def verify_session_ref(json_path: str | Path, trace: dict | SessionTrace | None) -> tuple[bool | None, str]:
    """(ok, message). ok is None when the trace has no session_ref sha256 (older outputs)."""
    t = trace.model_dump(mode="json") if isinstance(trace, SessionTrace) else (trace or {})
    ref = t.get("session_ref") or {}
    if not ref.get("sha256"):
        return None, "trace.session_ref has no sha256 (older output)"
    jp = Path(json_path)
    if ref.get("path") != jp.name:
        return False, f"trace.session_ref.path {ref.get('path')!r} != {jp.name!r}"
    got = session_content_sha256(json.loads(jp.read_text()))
    return (got == ref["sha256"]), (f"session_ref OK ({got[:16]}...)" if got == ref["sha256"] else
                                    f"trace.session_ref.sha256 {ref['sha256'][:16]}... != record content {got[:16]}...")


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
    # 1) hash the record content (no trace_ref yet), 2) write the trace with that hash, 3) pin the trace in trace_ref
    trace.session_ref = FileRef(path=jp.name, sha256=session_content_sha256(v11), sha256_covers=SESSION_REF_COVERS,
                                schema_version=v11["schema_version"])
    tp.write_text(trace.model_dump_json(indent=2) + "\n")
    tsha = sha256_file(tp)
    tp.with_name(tp.name + ".sha256").write_text(f"{tsha}  {tp.name}\n")
    v11["trace_ref"] = {"path": tp.name, "sha256": tsha}
    SessionRecordSpec.model_validate(v11)                    # never write a record Doodle's schema rejects
    jp.write_text(_dump(v11))
    ok, msg = verify_session_ref(jp, trace)
    if not ok:                                               # trace_ref is the only key added after hashing
        raise RuntimeError(msg)
    vp = v1_1_path(jp)
    if vp.exists():                                          # superseded preview from an older run of ours
        vp.unlink()
    mp.write_text(render_markdown(v11, trace))
    return Written(jp, mp, tp, sha256_file(jp), tsha)


def read_session(path: str | Path) -> dict:
    d = json.loads(Path(path).read_text())
    (SessionRecordSpec if d.get("schema_version") in ("1.1.0", "1.1.1") else SessionRecordV1).model_validate(d)
    return d


def read_session_for_append(json_path: str | Path) -> dict:
    """1.1.x record as-is; older outputs: the v1.1 preview if present, else the upgraded v1 record."""
    jp = Path(json_path)
    d = read_session(jp)
    if d.get("schema_version") in ("1.1.0", "1.1.1"):
        return strip_trace_ref(d)
    vp = v1_1_path(jp)
    return strip_trace_ref(read_session(vp)) if vp.exists() else from_v1(d)


def read_trace(path: str | Path) -> SessionTrace | None:
    p = Path(path)
    return SessionTrace.model_validate_json(p.read_text()) if p.exists() else None


def read_legacy_session(path: str | Path) -> LegacySessionRecord:
    return LegacySessionRecord.model_validate(json.loads(Path(path).read_text()))
