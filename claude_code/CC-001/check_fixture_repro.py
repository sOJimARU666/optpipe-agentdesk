#!/usr/bin/env python3
"""CC-001: reproducibility and integrity check for the documented offline fixture workflow. PAPER ONLY.

Lives in claude_code/ (see claude_code/README.md): nothing in optpipe imports it, and the main test suite and CI do
not collect it. Run it as  python claude_code/CC-001/check_fixture_repro.py  from the repo root.

It runs the README's 60-second demo the way a user runs it (the installed `optpipe` console script, one process
per command), once per cell of a TZ x PYTHONHASHSEED matrix:

    optpipe run --fixtures tests/fixtures/2026-10-13 --out <cell>/out
    optpipe validate <cell>/out/session-2026-10-13.json
    optpipe watch --session <cell>/out/session-2026-10-13.json --replay tests/fixtures/2026-10-13/watch_tape.yaml \
                  --out <cell>/out

Each cell gets its own temp out/, OPTPIPE_STATE_DIR and OPTPIPE_CACHE_DIR, so nothing is read from or written to
the repo's out/ or var/. It uses the pinned spec/ unless OPTIONS_SPEC_DIR is set, and the spec must pass the pin.

Per cell, with optpipe's own functions (nothing is re-implemented):
  C0  the cell got the environment it asked for (a probe process reports its UTC offset and hash seed)
  C1  run, validate and watch exit 0
  C2  the trace sidecar matches the trace bytes                  (watcher.runtime.verify_sidecar)
  C3  record.trace_ref = {trace file name, sha256 of its bytes}   [INV-11]
  C4  trace.session_ref matches the record content               (records.store.verify_session_ref)
  C5  the watch sidecar matches and validate_watch(doc) is empty (watcher.runtime.verify_sidecar, validate_watch)
  C6  watch.session = {record file name, sha256 of the record bytes}
  C7  every output is strict UTF-8 with LF line endings and a final newline (a text-mode write on Windows fails)
Across cells:
  R1  each output is parsed, only the VOLATILE fields below are masked, and the result is hashed as canonical JSON
      (sorted keys, no whitespace, UTF-8; Markdown: masked text). Every cell must give the same digests. Their
      combined digest is the workflow fingerprint. A teammate passes it as --expect to compare another machine.
  VOLATILE is the complete list of wall-clock fields, plus the hashes that cover them (C2-C6 verify those per cell).

Exit 0: every check passed (and the fingerprint equals --expect, if given). 1: a check failed. 2: the check could
not run (spec not pinned, optpipe not installed from this checkout, unknown time zone, bad arguments).
closure_claim: none. No network, no broker, no order path: it reads fixtures and writes temp files only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # claude_code/CC-001/<this file> -> repo root
TOOL = Path(__file__).resolve().relative_to(ROOT).as_posix()
TASK_ID = "CC-001"
RECEIPT_VERSION = 1
FIXTURES = "tests/fixtures/2026-10-13"
TAPE = f"{FIXTURES}/watch_tape.yaml"
DEFAULT_TZ = ("UTC", "Europe/Berlin", "America/New_York")
DEFAULT_SEEDS = ("0", "1")
STEP_TIMEOUT_S = 600
PROBE_INSTANT = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)   # fixed instant: offsets do not depend on today

# Wall-clock fields, and the hashes over them. Paths: dict keys, "*" = every list item.
VOLATILE = {
    "trace": (("runs", "*", "steps", "*", "started_at"), ("runs", "*", "steps", "*", "duration_ms")),
    "record": (("trace_ref", "sha256"),),
    "watch": (("session", "sha256"),),
}
MD_TIMING = re.compile(r"^(\d+\. \*\*[a-z_]+\*\* \(.*\)) \d+ ms\b", re.M)   # "1. **discovery** (...) 213 ms"
MASK = "<volatile>"
CHECKS = ("C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7")
# Files whose text form C7 checks; sidecars included.
OUTPUTS = ("record", "trace", "trace_sidecar", "markdown", "watch", "watch_sidecar")
DEPENDENCIES = ("pydantic", "PyYAML", "requests", "beautifulsoup4", "lxml", "fastapi", "Jinja2", "uvicorn",
                "jsonschema", "tzdata")
# Inputs that decide the outputs; their byte digest identifies the source even without git (e.g. an archive).
INPUT_GLOBS = ("src/optpipe/**/*", f"{FIXTURES}/*", "spec/MANIFEST.sha256", "spec/PIN.json", "config/settings.yaml",
               "pyproject.toml", TOOL)


class HarnessError(Exception):
    """The check cannot run here (exit 2)."""


# ------------------------------------------------------------------ normalisation and digests
def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask(doc, pattern: tuple) -> int:
    """Replace the value at every path matching `pattern` with MASK, in place. Returns how many were masked."""
    def walk(node, i: int) -> int:
        key = pattern[i]
        if key == "*":
            return sum(walk(x, i + 1) for x in node) if isinstance(node, list) else 0
        if not isinstance(node, dict) or key not in node:
            return 0
        if i == len(pattern) - 1:
            node[key] = MASK
            return 1
        return walk(node[key], i + 1)
    return walk(doc, 0)


def normalized_digest(kind: str, data: bytes) -> tuple[str, int]:
    """(sha256 of the masked canonical form, number of masked values) for one output file."""
    if kind == "markdown":
        text, n = MD_TIMING.subn(rf"\1 {MASK} ms", data.decode("utf-8"))
        return sha256(text.encode("utf-8")), n
    doc = json.loads(data.decode("utf-8"))
    n = sum(mask(doc, p) for p in VOLATILE.get(kind, ()))
    return sha256(canonical(doc)), n


def fingerprint(digests: dict[str, str]) -> str:
    return sha256(canonical(digests))


# ------------------------------------------------------------------ per-cell integrity
def output_paths(out: Path) -> dict[str, Path]:
    recs = sorted(p for p in out.glob("session-*.json") if p.name.count(".") == 1)
    if len(recs) != 1:
        raise FileNotFoundError(f"expected one session-<date>.json in the cell output, found {len(recs)}")
    stem = recs[0].name[:-len(".json")]
    return {"record": recs[0], "trace": out / f"{stem}.trace.json", "trace_sidecar": out / f"{stem}.trace.json.sha256",
            "markdown": out / f"{stem}.md", "watch": out / f"{stem}.watch.json",
            "watch_sidecar": out / f"{stem}.watch.json.sha256"}


def check_outputs(out: Path) -> dict:
    """C2-C7 and the normalised digests for one cell's out/ directory. Never raises: a check that cannot be
    evaluated is False with a reason (fail closed)."""
    from optpipe.records.store import verify_session_ref
    from optpipe.watcher.runtime import validate_watch, verify_sidecar

    checks = {c: False for c in CHECKS[2:]}
    errors: list[str] = []
    res = {"checks": checks, "errors": errors, "digests": {}, "masked": {}}
    try:
        files = output_paths(out)
        raw = {k: p.read_bytes() for k, p in files.items()}
    except OSError as e:
        errors.append(f"outputs unreadable: {type(e).__name__}: {e.strerror or e}")
        return res
    try:
        rec, trace, watch = (json.loads(raw[k].decode("utf-8")) for k in ("record", "trace", "watch"))
    except ValueError as e:
        errors.append(f"outputs do not parse: {type(e).__name__}: {e}")
        rec = trace = watch = None

    def check(cid: str, fn) -> None:
        try:
            ok, why = fn()
        except Exception as e:                   # noqa: BLE001 - any failure to evaluate is a failed check
            ok, why = False, f"{type(e).__name__}: {e}"
        checks[cid] = bool(ok)
        if not ok:
            errors.append(f"{cid}: {why}")

    rp, tp, wp = files["record"], files["trace"], files["watch"]
    check("C2", lambda: (verify_sidecar(tp), "trace sidecar does not match the trace bytes"))
    check("C3", lambda: (rec["trace_ref"] == {"path": tp.name, "sha256": sha256(raw["trace"])},
                         f"record.trace_ref {rec['trace_ref']} != {tp.name} at {sha256(raw['trace'])[:16]}..."))
    check("C4", lambda: verify_session_ref(rp, trace, rec))
    check("C5", lambda: (verify_sidecar(wp) and not validate_watch(watch),
                         f"watch sidecar ok={verify_sidecar(wp)}, validate_watch={validate_watch(watch)[:3]}"))
    check("C6", lambda: ((watch["session"]["path"], watch["session"]["sha256"]) == (rp.name, sha256(raw["record"])),
                         f"watch.session {watch['session'].get('path')} != {rp.name} at {sha256(raw['record'])[:16]}..."))

    def text_form():
        bad = []
        for k in OUTPUTS:
            data = raw[k]
            try:
                data.decode("utf-8", errors="strict")
            except UnicodeDecodeError as e:
                bad.append(f"{files[k].name} is not UTF-8 ({e.reason} at byte {e.start})")
            if b"\r" in data:
                bad.append(f"{files[k].name} has CR line endings")
            if not data.endswith(b"\n"):
                bad.append(f"{files[k].name} has no final newline")
        return not bad, "; ".join(bad)
    check("C7", text_form)

    for k in ("record", "trace", "markdown", "watch"):
        try:
            res["digests"][k], res["masked"][k] = normalized_digest(k, raw[k])
        except (ValueError, UnicodeDecodeError) as e:
            errors.append(f"R1: {files[k].name} cannot be normalised: {type(e).__name__}")
    return res


# ------------------------------------------------------------------ environment
def probe() -> dict:
    """What this process actually got (run by each cell as `<this script> --probe`)."""
    return {"tzname": list(time.tzname), "utc_offset_s": int(PROBE_INSTANT.astimezone().utcoffset().total_seconds()),
            "hash_randomization": sys.flags.hash_randomization, "hash_of_optpipe": hash("optpipe"),
            "preferred_encoding": locale.getpreferredencoding(False), "utf8_mode": sys.flags.utf8_mode}


def expected_offset(tz: str | None) -> int | None:
    if tz is None:
        return None
    from zoneinfo import ZoneInfo
    return int(PROBE_INSTANT.astimezone(ZoneInfo(tz)).utcoffset().total_seconds())


def env_applied(got: dict | None, tz: str | None, seed: str) -> tuple[bool, str]:
    """C0: did the cell's process get the TZ offset and hash seed it asked for? (tz None = host zone, unchecked)"""
    want = expected_offset(tz)
    seed_ok = bool(got) and got.get("hash_randomization") == (0 if int(seed) == 0 else 1)
    tz_ok = want is None or (bool(got) and got.get("utc_offset_s") == want)
    return seed_ok and tz_ok, (f"environment not applied: TZ {tz or '<host>'} offset "
                               f"{(got or {}).get('utc_offset_s')} (want {want}), PYTHONHASHSEED {seed} "
                               f"hash_randomization {(got or {}).get('hash_randomization')}")


def summarize(results: list[dict], expect: str | None) -> dict:
    """Verdict over all cells: every check passed in every cell, one fingerprint, and it equals --expect."""
    fps = {r["fingerprint"] for r in results}
    integrity_ok = bool(results) and all(all(r["checks"].values()) for r in results)
    reproducible = bool(results) and len(fps) == 1 and None not in fps
    fp = next(iter(fps)) if reproducible else None
    failures = [f"cell {r['TZ']},{r['PYTHONHASHSEED']}: {e}" for r in results for e in r["errors"]]
    if not reproducible:
        for k in ("record", "trace", "markdown", "watch"):
            if len({r["normalized_sha256"].get(k) for r in results}) > 1:
                failures.append(f"R1: {k} differs between cells after masking the volatile fields")
    expect_ok = None if expect is None else (fp == expect)
    if expect_ok is False:
        failures.append(f"--expect {expect[:16]}... != this run's fingerprint {(fp or 'none')[:16]}...")
    return {"integrity_ok": integrity_ok, "reproducible": reproducible, "fingerprint": fp,
            "expected_fingerprint": expect, "expect_match": expect_ok,
            "verdict": "PASS" if integrity_ok and reproducible and expect_ok is not False else "FAIL",
            "failures": failures}


def environment() -> dict:
    deps = {}
    for d in DEPENDENCIES:
        try:
            deps[d] = metadata.version(d)
        except metadata.PackageNotFoundError:
            deps[d] = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.platform(), "sys_platform": sys.platform, "machine": platform.machine(),
            "preferred_encoding": locale.getpreferredencoding(False), "utf8_mode": sys.flags.utf8_mode,
            "tz_axis": "exercised (time.tzset available)" if hasattr(time, "tzset") else
                       "not exercised: no time.tzset on this platform (TZ is POSIX only); cells use the host zone",
            "execution": "native OS processes (console script per command); not an emulation", "dependencies": deps}


def source_identity() -> dict:
    files = sorted({p for g in INPUT_GLOBS for p in ROOT.glob(g)
                    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"})
    manifest = "".join(f"{sha256(p.read_bytes())}  {p.relative_to(ROOT).as_posix()}\n" for p in files)
    ident = {"git_head": None, "git_tree": None, "worktree_dirty_paths": None,
             "inputs_sha256": sha256(manifest.encode("utf-8")), "inputs_files": len(files),
             "inputs_note": "sha256 of a sha256sum-style manifest of " + ", ".join(INPUT_GLOBS)
                            + " (bytes as checked out: a CRLF checkout gives another digest)"}
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        ident["git_note"] = "not a git checkout (or git not installed): identify the source by inputs_sha256"
        return ident

    def g(*args: str) -> str | None:
        r = subprocess.run([git, "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8",
                           check=False, shell=False, timeout=60)
        return r.stdout if r.returncode == 0 else None
    head, tree, status = g("rev-parse", "HEAD"), g("rev-parse", "HEAD^{tree}"), g("status", "--porcelain")
    ident["git_head"] = head.strip() if head else None
    ident["git_tree"] = tree.strip() if tree else None
    ident["worktree_dirty_paths"] = sorted(line[3:] for line in status.splitlines()) if status is not None else None
    return ident


def spec_identity(spec_dir: Path) -> dict:
    from optpipe.config import display_path
    from optpipe.spec_pin import pin_error
    err = pin_error(spec_dir)
    if err:
        raise HarnessError(err)
    pin = json.loads((spec_dir / "PIN.json").read_text(encoding="utf-8")) if (spec_dir / "PIN.json").is_file() else {}
    return {"dir": display_path(spec_dir), "version": (spec_dir / "VERSION").read_text(encoding="utf-8").strip(),
            "manifest_sha256": sha256((spec_dir / "MANIFEST.sha256").read_bytes()),
            "pin_json_manifest_sha256": pin.get("manifest_sha256")}


# ------------------------------------------------------------------ one cell
def run_cell(exe: str, tz: str | None, seed: str, cell: Path, spec_dir: Path, fixtures: Path, tape: Path,
             scrub) -> dict:
    out = cell / "out"
    env = dict(os.environ, PYTHONHASHSEED=seed, OPTPIPE_STATE_DIR=str(cell / "state"),
               OPTPIPE_CACHE_DIR=str(cell / "cache"), OPTIONS_SPEC_DIR=str(spec_dir), PYTHONDONTWRITEBYTECODE="1")
    if tz is not None:
        env["TZ"] = tz
    t0 = time.monotonic()
    res: dict = {"TZ": tz or "<host>", "PYTHONHASHSEED": seed, "exit": {}, "stderr_tail": {}}

    def call(name: str, argv: list[str]) -> int | str:
        try:
            r = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=STEP_TIMEOUT_S, check=False, shell=False)
        except subprocess.TimeoutExpired:
            res["exit"][name] = "timeout"
            return "timeout"
        (cell / "logs").mkdir(parents=True, exist_ok=True)
        (cell / "logs" / f"{name}.stdout").write_text(r.stdout, encoding="utf-8", newline="\n")
        (cell / "logs" / f"{name}.stderr").write_text(r.stderr, encoding="utf-8", newline="\n")
        res["exit"][name] = r.returncode
        if r.returncode != 0:
            res["stderr_tail"][name] = [scrub(x) for x in (r.stderr or r.stdout).strip().splitlines()[-5:]]
        return r.stdout if name == "probe" and r.returncode == 0 else r.returncode

    got = call("probe", [sys.executable, str(Path(__file__).resolve()), "--probe"])
    res["probe"] = json.loads(got) if isinstance(got, str) and got.startswith("{") else None
    res["probe_expected_utc_offset_s"] = expected_offset(tz)
    c0 = env_applied(res["probe"], tz, seed)

    rc_run = call("run", [exe, "run", "--fixtures", str(fixtures), "--out", str(out)])
    try:
        rp = output_paths(out)["record"]
    except FileNotFoundError:
        rp = out / "session-missing.json"
    rc_val = call("validate", [exe, "validate", str(rp)])
    rc_watch = call("watch", [exe, "watch", "--session", str(rp), "--replay", str(tape), "--out", str(out)])

    chk = check_outputs(out)
    chk["checks"] = {"C0": c0[0], "C1": (rc_run, rc_val, rc_watch) == (0, 0, 0), **chk["checks"]}
    if not c0[0]:
        chk["errors"].insert(0, f"C0: {c0[1]}")
    if not chk["checks"]["C1"]:
        chk["errors"].insert(0, f"C1: exit codes run={rc_run} validate={rc_val} watch={rc_watch}")
    res.update(checks=chk["checks"], errors=[scrub(e) for e in chk["errors"]], normalized_sha256=chk["digests"],
               masked_values=chk["masked"], fingerprint=fingerprint(chk["digests"]) if chk["digests"] else None,
               seconds=round(time.monotonic() - t0, 1))
    return res


# ------------------------------------------------------------------ main
def parse_cells(values: list[str] | None) -> list[tuple[str | None, str]]:
    if values:
        cells = []
        for v in values:
            tz, sep, seed = v.rpartition(",")
            if not sep or not tz or not re.fullmatch(r"[0-9]{1,10}", seed) or int(seed) > 4294967295:
                raise HarnessError(f"--cell {v!r}: expected TZ,SEED such as UTC,0 (SEED 0..4294967295)")
            cells.append((tz, seed))
        return cells
    tzs = DEFAULT_TZ if hasattr(time, "tzset") else (None,)
    return [(tz, s) for tz in tzs for s in DEFAULT_SEEDS]


def find_console_script() -> str:
    exe = shutil.which("optpipe", path=str(Path(sys.executable).parent))
    if exe is None:
        raise HarnessError("the `optpipe` console script is not installed next to this interpreter "
                           f"({Path(sys.executable).name}); run: python -m pip install -e '.[dev]'")
    return exe


def preflight(cells) -> None:
    try:
        import optpipe.config as cfg
    except ImportError as e:
        raise HarnessError(f"optpipe is not importable ({e}); run: python -m pip install -e '.[dev]'") from e
    if Path(cfg.REPO_ROOT).resolve() != ROOT:
        raise HarnessError("optpipe is installed from another checkout than this script's; "
                           "reinstall here with python -m pip install -e '.[dev]'")
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    for tz in {"America/New_York", "Europe/Berlin"} | {t for t, _ in cells if t}:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise HarnessError(f"time zone {tz!r} is not available ({type(e).__name__}); "
                               "on Windows install the tzdata package") from e
    if any(t for t, _ in cells) and not hasattr(time, "tzset"):
        raise HarnessError("this platform has no time.tzset, so a TZ in --cell would not be applied; "
                           "run without --cell to use the host zone")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--cell", action="append", metavar="TZ,SEED",
                    help="one matrix cell (repeatable). Default: TZ in "
                         f"{', '.join(DEFAULT_TZ)} x PYTHONHASHSEED in {', '.join(DEFAULT_SEEDS)}")
    ap.add_argument("--expect", metavar="SHA256", help="fingerprint from another run; exit 1 unless it matches")
    ap.add_argument("--receipt", metavar="FILE", help="write the JSON receipt here")
    ap.add_argument("--keep", metavar="DIR", help="keep every cell's outputs and logs in DIR (new or empty)")
    ap.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.probe:
        print(json.dumps(probe()))
        return 0
    try:
        if a.expect is not None and not re.fullmatch(r"[0-9a-f]{64}", a.expect):
            raise HarnessError("--expect needs a full 64-character lowercase hex sha256")
        cells = parse_cells(a.cell)
        preflight(cells)
        exe = find_console_script()
        env_dir = os.environ.get("OPTIONS_SPEC_DIR")
        spec_dir = Path(env_dir).resolve() if env_dir else ROOT / "spec"
        spec = spec_identity(spec_dir)
        if a.keep:
            work = Path(a.keep).resolve()
            if work.exists() and any(work.iterdir()):
                raise HarnessError("--keep DIR must be new or empty")
            work.mkdir(parents=True, exist_ok=True)
        else:
            work = Path(tempfile.mkdtemp(prefix="optpipe-cc001-"))
    except HarnessError as e:
        print(f"check_fixture_repro: cannot run: {e}", file=sys.stderr)
        return 2

    def scrub(text: str) -> str:            # receipts stay shareable: no absolute paths (also the resolved forms)
        for path, label in ((work.resolve(), "<work>"), (work, "<work>"), (ROOT, "<repo>")):
            text = text.replace(str(path), label)
        return text

    try:
        started = datetime.now(timezone.utc)
        source = source_identity()
        results = []
        for i, (tz, seed) in enumerate(cells):
            slug = re.sub(r"[^A-Za-z0-9]+", "-", tz or "host").strip("-")
            results.append(run_cell(exe, tz, seed, work / f"cell{i}-{slug}-seed{seed}", spec_dir, ROOT / FIXTURES,
                                    ROOT / TAPE, scrub))
    except Exception as e:                       # noqa: BLE001 - a harness crash is exit 2, never a verdict
        print(f"check_fixture_repro: crashed: {type(e).__name__}: {scrub(str(e))}", file=sys.stderr)
        return 2
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)

    s = summarize(results, a.expect)
    fp, failures, verdict, reproducible, expect_ok = (s["fingerprint"], s["failures"], s["verdict"],
                                                      s["reproducible"], s["expect_match"])

    receipt = {
        "task_id": TASK_ID, "receipt_version": RECEIPT_VERSION, "tool": TOOL,
        "closure_claim": "none", "paper_only": True, "generated_at_utc": started.isoformat(timespec="seconds"),
        "source": source, "spec": spec, "environment": environment(),
        "workflow": {"fixtures": FIXTURES, "tape": TAPE, "commands": [
            f"optpipe run --fixtures {FIXTURES} --out <cell>/out",
            "optpipe validate <cell>/out/session-<date>.json",
            f"optpipe watch --session <cell>/out/session-<date>.json --replay {TAPE} --out <cell>/out"]},
        "volatile_fields": {**{k: ["/".join(p) for p in v] for k, v in VOLATILE.items()},
                            "markdown": [MD_TIMING.pattern]},
        "checks": {"C0": "environment applied", "C1": "exit 0 x3", "C2": "trace sidecar", "C3": "record.trace_ref",
                   "C4": "trace.session_ref", "C5": "watch sidecar + validate_watch", "C6": "watch.session",
                   "C7": "UTF-8, LF, final newline", "R1": "same masked digests in every cell"},
        "cells": results, **s, "kept_outputs": "<work> = " + Path(a.keep).name if a.keep else None,
    }
    if a.receipt:
        Path(a.receipt).write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                   newline="\n")

    head = (source["git_head"] or "no-git")[:12]
    dirty = source["worktree_dirty_paths"]
    state = "clean" if dirty == [] else f"{len(dirty)} changed path(s)" if dirty else "unknown"
    print(f"PAPER ONLY | {TASK_ID} fixture workflow reproducibility | source {head} ({state}) | inputs "
          f"{source['inputs_sha256'][:12]} | spec {spec['version']} {spec['manifest_sha256'][:12]}")
    for r in results:
        bad = [c for c, ok in r["checks"].items() if not ok]
        print(f"  TZ {r['TZ']:18} seed {r['PYTHONHASHSEED']:>3}  exit {r['exit'].get('run')}/{r['exit'].get('validate')}/"
              f"{r['exit'].get('watch')}  {'C0-C7 OK' if not bad else 'FAILED ' + ','.join(bad)}  "
              f"{(r['fingerprint'] or 'no digest')[:16]}  {r['seconds']}s")
    for f in failures[:30]:
        print(f"  FAIL {f}")
    print(f"reproducible across {len(results)} cell(s): {'yes' if reproducible else 'NO'}"
          + (f" | fingerprint {fp}" if fp else ""))
    if a.expect:
        print(f"--expect: {'match' if expect_ok else 'MISMATCH'}")
    if a.receipt:
        print(f"receipt: {a.receipt}")
    print(f"verdict: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
