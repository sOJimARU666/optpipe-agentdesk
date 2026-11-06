"""CLI entry point. PAPER ONLY - there is no command that places, sends or routes an order.

  optpipe run [--fixtures DIR | --data-dir ROOT] [--inputs FILE] [--as-of TS] [--out DIR] [--append]
  optpipe serve [--out DIR] [--host 127.0.0.1] [--port 8765]   # read-only dashboard, loopback only
  optpipe watch [--session FILE | --date D] (--replay TAPE [--clock TS] [--until TS] | --data-dir ROOT) [--once] [--out DIR]
                                     paper-only watcher: alerts only; quotes from a replay tape or an Oodle pack
  optpipe spec-check                 what was found in Doodle's spec folder + ruleset placeholders
  optpipe validate FILE.json         run Doodle's validate_session.py on a record
  optpipe show FILE.json             print the Markdown for a record
  optpipe market-data [--data-dir ROOT] [--date D] [--now TS]
                                     verify + validate one of Oodle's market-data packs (file I/O only, no network)
"""
from __future__ import annotations

from .scenario import tag_for_record

import argparse
import re
import sys
from datetime import date as _date, datetime, timezone
from pathlib import Path

from .config import REPO_ROOT, load_settings
from .records import paths, read_session, read_session_for_append, read_trace, render_markdown, write_session
from .records.fields import BERLIN, NY
from .records.guards import merge, review_view

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
        o = review_view(r) if r else None       # verdict + ids recomputed from the rule results (SOL-ACCEPT-001 G2)
        why = (" <- " + ", ".join(o["blocking_rule_ids"])) if o and o["blocking_rule_ids"] else ""
        flags = (" flags " + ", ".join(o["flag_rule_ids"])) if o and o["flag_rule_ids"] else ""
        why += " [INV-16 tampered: stored " + str(o["stored_verdict"]) + "]" if o and o["tampered"] else ""
        lines.append(f"  {o['verdict'] if o else '-':5} {tk['ticket_id']:30} qty {tk['quantity']} "
                     f"limit {tk['limit_price']['amount']} {tk['limit_price']['price_type']} "
                     f"max loss ${tk['max_loss']['amount_usd']}{why}{flags}")
    lines.append(f"recommendation: {rt.recommendation}")
    dq = rec["data_quality"]
    lines.append(f"data quality: {len(dq['missing'])} missing, {len(dq['stale'])} stale, "
                 f"{len(dq['conflicting'])} conflicting")
    v = rt.validation
    ok = not v.get("errors") and bool(v.get("validator"))     # never OK without the validator (FIX-SEC-001 P0-5)
    lines.append(f"validate_session: {'OK' if ok else str(len(v.get('errors') or [])) + ' error(s)'}"
                 f"{'' if v.get('validator') else ' (spec validator unavailable: NOT validated)'} (record {rec['schema_version']}, Doodle's schema)")
    lines.append("trace: " + " -> ".join(f"{s.stage}({s.duration_ms:.0f}ms)" for s in rt.steps))
    return "\n".join(lines)


LOOPBACK = {"127.0.0.1", "::1"}   # literal loopback addresses only; no hostnames (FIX-SEC-001)


def serve(out: str, host: str, port: int) -> int:
    """Run the dashboard with uvicorn. Refuses any bind other than 127.0.0.1 or ::1 (no all-interfaces bind, no hostnames)."""
    if host not in LOOPBACK:
        print(f"refusing to bind {host!r}: the dashboard is local-only (use 127.0.0.1 or ::1)", file=sys.stderr)
        return 2
    import uvicorn
    from .dashboard import create_app
    print(f"optpipe dashboard (paper only, read-only) -> http://{host}:{port}/  reading {Path(out).resolve()}")
    uvicorn.run(create_app(out), host=host, port=port, log_level="warning")
    return 0


def watch(a, settings) -> int:
    import json
    import yaml
    from .risk import load_ruleset
    from .risk.engine import Calendar
    from .spec import discover
    from .watcher.runtime import (PackQuoteSource, RealClock, ReplayTape, SimClock, Watcher, get_engine,
                                  validate_watch, verify_sidecar, write_watch)
    from . import marketdata as md
    data_dir = Path(a.data_dir) if getattr(a, "data_dir", None) else md.data_root_from_env()
    if bool(a.replay) == bool(data_dir):
        print("choose exactly one of --replay TAPE or --data-dir ROOT (else $OPTPIPE_MARKET_DATA_DIR)",
              file=sys.stderr); return 2
    sp = discover(settings.spec_path)
    rules = sp.watcher_rules()
    if rules is None:
        print("spec watcher_rules.yaml not found", file=sys.stderr); return 2
    cal = Calendar(load_ruleset(sp)[0].config.market_hours)
    tape_date = str(yaml.safe_load(Path(a.replay).read_text()).get("date")) if a.replay else None
    day = a.date or (a.clock[:10] if a.clock else None) or tape_date or datetime.now(NY).date().isoformat()
    sess = Path(a.session) if a.session else Path(a.out) / f"session-{day}.json"
    if not sess.exists():
        print(f"session file not found: {sess}", file=sys.stderr); return 2
    # Fail closed (FIX-SEC-001 P0-4): never watch a record Doodle's validator (or the optpipe guards) rejects.
    # The file is read and parsed ONCE: the object validated is the object watched, and the watch document's
    # session sha256 is of those same bytes (SOL-ACCEPT-001 G7, no re-read between validate and watch).
    import hashlib
    from .orchestrator import validate_record
    try:
        raw = sess.read_bytes()
        rec = json.loads(raw)
    except (OSError, ValueError) as e:
        print(f"optpipe watch: refusing to watch {sess.name}: unreadable ({type(e).__name__}: {e})", file=sys.stderr)
        return 2
    verrs = validate_record(settings, rec, sess.parent)  # Doodle's validator + records/guards.check_record, merged
    if verrs:
        print(f"optpipe watch: refusing to watch {sess.name}: the session record does not validate "
              f"({len(verrs)} error(s))\n  " + "\n  ".join(verrs), file=sys.stderr)
        return 2
    if data_dir is not None:
        src = PackQuoteSource(data_dir, spec=sp)
        clock = RealClock() if not a.clock else None
        if a.clock:
            start = datetime.fromisoformat(a.clock)
            if start.tzinfo is None:
                print("--clock needs a UTC offset", file=sys.stderr); return 2
            clock = SimClock(start)
        else:
            clock = RealClock()
    else:
        src = ReplayTape(a.replay)
        start = datetime.fromisoformat(a.clock) if a.clock else datetime.fromisoformat(f"{day}T07:00:00").replace(tzinfo=NY)
        if start.tzinfo is None:
            print("--clock needs a UTC offset", file=sys.stderr); return 2
        clock = SimClock(start)
    s0 = clock.now()
    until = (datetime.fromisoformat(a.until) if a.until else
             datetime.combine(s0.astimezone(NY).date(), datetime.min.time(), NY).replace(hour=17))
    wt = Watcher(rec, rules, cal, src, get_engine(a.engine), session_path=sess, now=s0,
                 session_sha256=hashlib.sha256(raw).hexdigest())
    doc = wt.run(clock, until, once=a.once)
    res = write_watch(doc, sess, Path(a.out))
    sm = doc["summary"]
    t0 = s0.astimezone(NY)
    print(f"PAPER ONLY | watch {doc['session']['id']} | {doc['mode']['clock']} clock, quotes {doc['mode']['quotes']} "
          f"(GET only) | from {t0:%Y-%m-%d %H:%M} New York / {t0.astimezone(BERLIN):%H:%M %Z} Berlin")
    for t in doc["targets"]:
        flag = "VETOED, shadow" if t["vetoed"] else ("HYPOTHETICAL, watched" if t["hypothetical"] else t["status"])
        print(f"  {t['target_id']:30} {flag}")
    print(f"wakes {sm['wakes']} | quote calls {sm['quote_calls']} | alerts {sm['alerts']} {sm['by_level']}")
    for al in doc["alerts"][:40]:
        at = datetime.fromisoformat(al["at"]["ny"])
        print(f"  {at:%m-%d %H:%M} NY / {at.astimezone(BERLIN):%H:%M} Berlin  {al['message']}")
    for n in doc["notes"]:
        print(f"note: {n}")
    errs = validate_watch(doc)
    ok = verify_sidecar(res.json_path)
    print(f"wrote {res.json_path}  sha256 {res.sha256}\nwrote {res.json_path}.sha256 (sidecar {'OK' if ok else 'MISMATCH'})")
    print("validate_watch: " + ("OK" if not errs else f"{len(errs)} error(s)\n  " + "\n  ".join(errs)))
    return 1 if (errs or not ok) else 0


def market_data(a, settings) -> int:
    """FEAT-OODLE-01 PR A: load one date dir with optpipe.marketdata (fail closed). Exit 0 valid / 1 rule errors /
    2 unreadable, crash or spec not pinned. Paper only: pure file I/O; nothing else reads this pack yet (PR B)."""
    from zoneinfo import ZoneInfo
    from . import marketdata as md
    from .config import display_path
    from .spec import discover
    if a.now:
        try:
            now = datetime.fromisoformat(a.now)
        except ValueError:
            print(f"--now {a.now!r} is not an ISO timestamp", file=sys.stderr); return 2
        if now.tzinfo is None or now.utcoffset() is None:
            print("--now needs a UTC offset (MD-19-TZ)", file=sys.stderr); return 2
    else:
        now = datetime.now(timezone.utc)
    root = Path(a.data_dir) if a.data_dir else md.data_root_from_env()
    if root is None:   # no live folder by default: nothing is opened (PR #22 review)
        res = md.load_date(None, a.date)
        for e in res.errors:
            print(e, file=sys.stderr)
        return 2
    if a.date is not None:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", a.date):
            print(f"--date {a.date!r} is not YYYY-MM-DD", file=sys.stderr); return 2
        try:
            _date.fromisoformat(a.date)
        except ValueError:
            print(f"--date {a.date!r} is not a real calendar date", file=sys.stderr); return 2
    date = a.date or now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    res = md.load_date(root, date, now=now, spec=discover(settings.spec_path))
    shown = display_path(root / date)
    for e in res.errors:
        print(e, file=sys.stderr)
    rc = res.exit_code()
    if rc:
        print(f"FAIL: {shown}: {len(res.errors)} error(s), exit {rc} (fail closed: nothing from this pack is used)",
              file=sys.stderr)
        return rc
    print(f"OK: {shown} valid (market_snapshot pack, schema_version {res.schema_version}, "
          f"data_label {res.index.get('data_label')}, {len(res.snapshots)} ticker(s), checked at {now.isoformat()})")
    for t in sorted(res.snapshots):
        why = md.no_idea_reasons(res, t, now=now)
        print(f"  {t:8} {'inputs usable' if not why else 'NO IDEA: ' + md.error_id(why[0])}"
              + (f" (+{len(why) - 1} more)" if len(why) > 1 else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="optpipe", description="Paper-only options research pipeline (no orders, ever)")
    ap.add_argument("--config", help="settings.yaml path")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one pipeline pass (offline from fixtures by default, or from an Oodle pack)")
    r.add_argument("--fixtures", default=None, help=f"fixture dir (offline; default {DEFAULT_FIXTURES} when no --data-dir)")
    r.add_argument("--data-dir", help="Oodle market-data root (YYYY-MM-DD dirs); else $OPTPIPE_MARKET_DATA_DIR. "
                                      "Mutually exclusive with --fixtures. Pack mode replaces the retired live HTTP path.")
    r.add_argument("--inputs", help="session inputs YAML (default: <fixtures>/session_inputs.yaml)")
    r.add_argument("--as-of", help="ISO timestamp with offset; default: inputs as_of (fixtures/packs) or now")
    r.add_argument("--out", default=str(REPO_ROOT / "out"))
    r.add_argument("--append", action="store_true", help="add this run to an existing session file for the date")
    r.add_argument("--allow-example", action="store_true",
                   help="allow EXAMPLE/TEST pack labels (tests / demos only; never a real idea)")
    sub.add_parser("spec-check", help="report Doodle's spec folder status")
    for name in ("show", "validate"):
        sub.add_parser(name).add_argument("file")
    sv = sub.add_parser("serve", help="read-only local dashboard over out/ (GET only, loopback only)")
    sv.add_argument("--out", default=str(REPO_ROOT / "out"))
    sv.add_argument("--host", default="127.0.0.1", help="loopback only: 127.0.0.1 or ::1")
    sv.add_argument("--port", type=int, default=8765)
    w = sub.add_parser("watch", help="paper-only watcher over a session's tickets (alert only, no order)")
    w.add_argument("--session", help="session JSON (default: <out>/session-<date>.json)")
    w.add_argument("--date", help="session date YYYY-MM-DD (default: the replay tape's date)")
    w.add_argument("--out", default=str(REPO_ROOT / "out"), help="where <session>.watch.json is written")
    w.add_argument("--replay", help="replay quote tape YAML (offline, no network)")
    w.add_argument("--clock", help="simulated start time, ISO with offset (replay only; default 07:00 NY of the date)")
    w.add_argument("--until", help="stop time, ISO with offset (default 17:00 NY of the start date)")
    w.add_argument("--once", action="store_true", help="a single wake at --clock")
    w.add_argument("--data-dir", help="Oodle market-data root for pack quotes (else $OPTPIPE_MARKET_DATA_DIR); "
                                      "mutually exclusive with --replay. Pack quotes replace the retired live HTTP path (PR B).")
    w.add_argument("--engine", default="spec", help="rule engine name (pluggable; default: spec)")
    m = sub.add_parser("market-data", help="verify one of Oodle's market-data packs (fail closed; file I/O only)")
    m.add_argument("--data-dir", help="market-data root holding YYYY-MM-DD dirs (else $OPTPIPE_MARKET_DATA_DIR; "
                                      "there is no default: with neither, exit 2 and nothing is opened)")
    m.add_argument("--date", help="pack date YYYY-MM-DD (default: today in New York)")
    m.add_argument("--now", help="the clock, ISO with offset (default: the real clock); used for MD-19 checks")
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
        # SOL-ACCEPT-001 G8: a spec folder that is not the pinned release, or has no usable validator, is a FAIL.
        from .orchestrator import load_validator
        _, verr = load_validator(sp)
        if verr:
            print(f"spec_pin           FAIL\n{verr}", file=sys.stderr)
            return 2
        from .spec_pin import EXPECTED_VERSION
        print(f"spec_pin           OK (pinned release {EXPECTED_VERSION}, every MANIFEST.sha256 file verified)")
        return 0
    if a.cmd == "watch":
        return watch(a, settings)
    if a.cmd == "market-data":
        return market_data(a, settings)
    if a.cmd == "show":
        print(render_markdown(read_session(a.file), read_trace(str(a.file).replace(".json", ".trace.json"))))
        return 0
    if a.cmd == "validate":
        # exit codes as Doodle's validate_session CLI (spec 1.1.3): 0 = valid, 1 = record errors,
        # 2 = unreadable input, missing/broken validator or a validator crash. Any non-zero is FAIL.
        import json
        from .orchestrator import ValidatorCrashed, load_validator, run_validator
        from .records.guards import check_record, guard_crashed
        mod, err = load_validator(discover(settings.spec_path))
        if mod is None:
            print(err, file=sys.stderr); return 2
        try:
            rec = json.loads(Path(a.file).read_text())
        except (OSError, ValueError) as e:
            print(f"SPEC-RECORD-UNREADABLE: {a.file}: {type(e).__name__}: {e} (fail closed)", file=sys.stderr); return 2
        try:
            errs = run_validator(mod, json.loads(json.dumps(rec)), base_dir=Path(a.file).resolve().parent)
        except ValidatorCrashed as e:
            print(str(e), file=sys.stderr); return 2
        errs = merge(errs, check_record(rec) if isinstance(rec, dict) else [])
        if guard_crashed(errs):                # a guard crash is like a validator crash: exit 2 (SOL-ACCEPT-001 G5)
            print("\n".join(errs), file=sys.stderr); return 2
        from .records.store import verify_session_ref
        tp = Path(str(a.file).replace(".json", ".trace.json"))
        if tp.exists():
            ok, msg = verify_session_ref(a.file, json.loads(tp.read_text()), rec)
            if ok is False:
                errs.append(f"session_ref: {msg}")
            elif ok:
                print(msg)
        print("\n".join(errs) if errs else "OK: 0 errors")
        return 1 if errs else 0

    from .orchestrator import load_inputs, load_validator, run_session
    _, verr = load_validator(discover(settings.spec_path))
    if verr:                                   # fail closed before anything is run or written (FIX-SEC-001 P0-5)
        print(f"optpipe run: refusing to run without Doodle's session validator.\n{verr}", file=sys.stderr)
        return 2
    from . import marketdata as md
    data_dir = Path(a.data_dir) if a.data_dir else md.data_root_from_env()
    if a.fixtures is not None and data_dir is not None:
        print("choose one of --fixtures or --data-dir (else $OPTPIPE_MARKET_DATA_DIR)", file=sys.stderr); return 2
    if data_dir is None:
        fixtures = Path(a.fixtures) if a.fixtures else DEFAULT_FIXTURES
    else:
        fixtures = None
    ip = a.inputs or (fixtures / "session_inputs.yaml" if fixtures and (fixtures / "session_inputs.yaml").exists() else None)
    inputs = load_inputs(ip)
    as_of = (datetime.fromisoformat(a.as_of) if a.as_of else
             inputs.as_of if inputs.as_of else datetime.now(timezone.utc))
    existing = trace = None
    if a.append:
        jp, _, tp = paths(a.out, as_of.astimezone(NY).date().isoformat())
        if jp.exists():
            existing, trace = read_session_for_append(jp), read_trace(tp)
    allow = ("MARKET", "EXAMPLE", "TEST") if a.allow_example else ("MARKET",)
    rec, trace = run_session(settings, inputs, as_of, fixtures, existing, trace,
                             data_dir=data_dir, allow_labels=allow)
    w = write_session(rec, trace, a.out)
    print(summary(rec, trace))
    from .orchestrator import validate_file
    ferrs = validate_file(settings, w.json)
    print(f"wrote {w.json}  sha256 {w.json_sha256}  (schema {rec['schema_version']}, trace_ref -> {w.trace.name})\n"
          f"wrote {w.trace}  sha256 {w.trace_sha256}\nwrote {w.md}")
    if ferrs is not None:                      # always a list now; a missing validator is an error in it
        print(f"validate_session {w.json.name}: " + (f"OK as {rec['schema_version']} (schema + invariants INV-01..{ {'1.1.8': '17', '1.1.7': '17', '1.1.6': '17', '1.1.5': '16', '1.1.4': '16', '1.1.3': '16', '1.1.2': '15', '1.1.1': '14'}.get(rec['schema_version'], '13') })" if not ferrs
                                                     else f"{len(ferrs)} error(s)\n  " + "\n  ".join(ferrs[:20])))
    v = trace.runs[-1].validation
    from .records.guards import guard_crashed
    if guard_crashed(ferrs or []):
        return 2
    return 1 if (v.get("errors") or ferrs) else 0


if __name__ == "__main__":
    sys.exit(main())
