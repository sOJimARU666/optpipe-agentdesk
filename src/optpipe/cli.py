"""CLI entry point. PAPER ONLY - there is no command that places, sends or routes an order.

  optpipe run [--fixtures DIR] [--inputs FILE] [--as-of TS] [--out DIR] [--append] [--live]
  optpipe serve [--out DIR] [--host 127.0.0.1] [--port 8765]   # read-only dashboard, loopback only
  optpipe spec-check                 what was found in Doodle's spec folder + ruleset placeholders
  optpipe validate FILE.json         run Doodle's validate_session.py on a record
  optpipe show FILE.json             print the Markdown for a record
"""
from __future__ import annotations

from .scenario import tag_for_record

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT, load_settings
from .records import paths, read_session, read_session_for_append, read_trace, render_markdown, write_session
from .records.fields import BERLIN, NY

DEFAULT_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "2026-10-13"


def summary(rec: dict, trace) -> str:
    rt = trace.runs[-1]
    run = rec["runs"][-1]
    t = datetime.fromisoformat(run["started"]["ny"])
    tag = tag_for_record(rec)
    lines = [f"PAPER ONLY{' | ' + tag if tag else ''} | {rec['session']['id']} | run {run['run_id']} ({run['phase']}, {rt.data_mode}) | "
             f"{t:%H:%M} New York / {t.astimezone(BERLIN):%H:%M %Z} Berlin",
             "candidates: " + ", ".join(c["underlying"] for c in rec["candidates"])]
    reviews = {r["ticket_id"]: r for r in rec["risk_reviews"] if r["run_id"] == run["run_id"]}
    for tk in rec["tickets"]:
        r = reviews.get(tk["ticket_id"])
        o = r["overall"] if r else None
        why = (" <- " + ", ".join(o["blocking_rule_ids"])) if o and o["blocking_rule_ids"] else ""
        flags = (" flags " + ", ".join(o["flag_rule_ids"])) if o and o["flag_rule_ids"] else ""
        lines.append(f"  {o['verdict'] if o else '-':5} {tk['ticket_id']:30} qty {tk['quantity']} "
                     f"limit {tk['limit_price']['amount']} {tk['limit_price']['price_type']} "
                     f"max loss ${tk['max_loss']['amount_usd']}{why}{flags}")
    lines.append(f"recommendation: {rt.recommendation}")
    from .records import to_v1
    dq = to_v1(rec)["data_quality"]
    lines.append(f"data quality (v1 record): {len(dq['missing'])} missing, {len(dq['stale'])} stale, "
                 f"{len(dq['conflicting'])} conflicting")
    v = rt.validation
    lines.append(f"validate_session: {'OK' if not v.get('errors') else str(len(v['errors'])) + ' error(s)'}"
                 f"{'' if v.get('validator') else ' (spec validator not found)'} (v1 record, Doodle's schema)")
    if "v1_1_errors" in v:
        lines.append(f"validate_session: {'OK' if not v['v1_1_errors'] else str(len(v['v1_1_errors'])) + ' error(s)'}"
                     f" (v1.1 preview, {v['v1_1_schema']})")
    lines.append("trace: " + " -> ".join(f"{s.stage}({s.duration_ms:.0f}ms)" for s in rt.steps))
    return "\n".join(lines)


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def serve(out: str, host: str, port: int) -> int:
    """Run the dashboard with uvicorn. Refuses any non-loopback bind (no 0.0.0.0)."""
    if host not in LOOPBACK:
        print(f"refusing to bind {host!r}: the dashboard is local-only (use 127.0.0.1)", file=sys.stderr)
        return 2
    import uvicorn
    from .dashboard import create_app
    print(f"optpipe dashboard (paper only, read-only) -> http://{host}:{port}/  reading {Path(out).resolve()}")
    uvicorn.run(create_app(out), host=host, port=port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="optpipe", description="Paper-only options research pipeline (no orders, ever)")
    ap.add_argument("--config", help="settings.yaml path")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one pipeline pass (offline from fixtures by default)")
    r.add_argument("--fixtures", default=str(DEFAULT_FIXTURES), help="fixture dir (offline)")
    r.add_argument("--inputs", help="session inputs YAML (default: <fixtures>/session_inputs.yaml)")
    r.add_argument("--as-of", help="ISO timestamp with offset; default: inputs as_of (fixtures) or now (live)")
    r.add_argument("--out", default=str(REPO_ROOT / "out"))
    r.add_argument("--append", action="store_true", help="add this run to an existing session file for the date")
    r.add_argument("--live", action="store_true",
                   help="EXPERIMENTAL: read-only HTTP GETs to Finviz/Nasdaq instead of fixtures")
    sub.add_parser("spec-check", help="report Doodle's spec folder status")
    for name in ("show", "validate"):
        sub.add_parser(name).add_argument("file")
    sv = sub.add_parser("serve", help="read-only local dashboard over out/ (GET only, loopback only)")
    sv.add_argument("--out", default=str(REPO_ROOT / "out"))
    sv.add_argument("--host", default="127.0.0.1", help="loopback only: 127.0.0.1, ::1 or localhost")
    sv.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        return serve(a.out, a.host, a.port)
    settings = load_settings(a.config)

    from .spec import discover
    if a.cmd == "spec-check":
        from .risk import load_ruleset
        sp = discover(settings.spec_path)
        for k, v in sp.status().items():
            print(f"{k:18} {v}")
        _, notes = load_ruleset(sp)
        for n in notes:
            print(f"note               {n}")
        return 0
    if a.cmd == "show":
        print(render_markdown(read_session(a.file), read_trace(str(a.file).replace(".json", ".trace.json"))))
        return 0
    if a.cmd == "validate":
        import json
        mod = discover(settings.spec_path).load_module("session_validator")
        if mod is None:
            print("spec validate_session.py not found"); return 2
        errs = mod.validate(json.loads(Path(a.file).read_text()))
        print("\n".join(errs) if errs else "OK: 0 errors")
        return 1 if errs else 0

    from .orchestrator import load_inputs, run_session
    fixtures = None if a.live else Path(a.fixtures)
    ip = a.inputs or (fixtures / "session_inputs.yaml" if fixtures and (fixtures / "session_inputs.yaml").exists() else None)
    inputs = load_inputs(ip)
    as_of = (datetime.fromisoformat(a.as_of) if a.as_of else
             inputs.as_of if (inputs.as_of and not a.live) else datetime.now(timezone.utc))
    existing = trace = None
    if a.append:
        jp, _, tp = paths(a.out, as_of.astimezone(NY).date().isoformat())
        if jp.exists():
            existing, trace = read_session_for_append(jp), read_trace(tp)
    rec, trace = run_session(settings, inputs, as_of, fixtures, existing, trace)
    w = write_session(rec, trace, a.out)
    print(summary(rec, trace))
    print(f"wrote {w.json}  sha256 {w.json_sha256}\nwrote {w.trace}  sha256 {w.trace_sha256}\n"
          f"wrote {w.v1_1}  (v1.1 preview, trace_ref -> {w.trace.name})\nwrote {w.md}")
    v = trace.runs[-1].validation
    return 1 if (v.get("errors") or v.get("v1_1_errors")) else 0


if __name__ == "__main__":
    sys.exit(main())
