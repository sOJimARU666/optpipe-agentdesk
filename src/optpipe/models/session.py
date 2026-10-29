"""Hand-off trace (one file per session, next to the schema-v1 record). Owner: Moodle (flow) / Poodle (code).

The trace is NOT part of session.schema.json v1 (top-level additionalProperties=false), so it lives in
`session-<date>.trace.json` and is rendered into the Markdown. Reported to Doodle as a schema gap.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from .common import Model


class HandoffTrace(Model):
    seq: int
    run_id: str
    stage: str
    agent: str
    owner: str
    started_at: datetime            # wall clock
    duration_ms: float
    inputs_summary: dict[str, Any] = Field(default_factory=dict)
    outputs_summary: dict[str, Any] = Field(default_factory=dict)
    verdict: Optional[str] = None
    error: Optional[str] = None


class RunTrace(Model):
    run_id: str
    as_of: datetime
    data_mode: str                  # fixtures | live
    steps: list[HandoffTrace] = Field(default_factory=list)
    recommendation: str = ""
    notes: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


class SessionTrace(Model):
    session_id: str
    trace_version: str = "optpipe-trace/1"
    runs: list[RunTrace] = Field(default_factory=list)
