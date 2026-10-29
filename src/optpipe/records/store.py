"""Persistence: schema-v1 session JSON (canonical) + Markdown (rendered) + hand-off trace JSON.

Owner: Doodle (schema) / Poodle (code). Writes only to the local output directory.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models.legacy import LegacySessionRecord
from ..models.schema_v1 import SessionRecordV1
from ..models.session import SessionTrace
from .markdown import render_markdown


def paths(out_dir: str | Path, date: str) -> tuple[Path, Path, Path]:
    out = Path(out_dir)
    stem = f"session-{date}"
    return out / f"{stem}.json", out / f"{stem}.md", out / f"{stem}.trace.json"


def write_session(rec: dict, trace: SessionTrace, out_dir: str | Path) -> tuple[Path, Path, Path]:
    SessionRecordV1.model_validate(rec)                      # never write an invalid record
    jp, mp, tp = paths(out_dir, rec["session"]["date"])
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    tp.write_text(trace.model_dump_json(indent=2) + "\n")
    mp.write_text(render_markdown(rec, trace))
    return jp, mp, tp


def read_session(path: str | Path) -> dict:
    d = json.loads(Path(path).read_text())
    SessionRecordV1.model_validate(d)
    return d


def read_trace(path: str | Path) -> SessionTrace | None:
    p = Path(path)
    return SessionTrace.model_validate_json(p.read_text()) if p.exists() else None


def read_legacy_session(path: str | Path) -> LegacySessionRecord:
    return LegacySessionRecord.model_validate(json.loads(Path(path).read_text()))
