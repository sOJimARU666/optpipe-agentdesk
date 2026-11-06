"""Subprocess replay for Sol item 7 (test_r7_7).

One cell: private labeled temp packs, fixed injected clocks, discovery -> research -> watcher.
The parent test runs this file under PYTHONHASHSEED x TZ. It does not read the wall clock.
closure_claim: none. This driver does not close R7, MD-18, R6, or paper, and it does not change policy.

Raw case files are json.dumps of an insertion-ordered object plus a newline (no sort_keys).
Semantic case files are normalization id_status_v1 (sorted id lists; error text dropped).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# tests/ is not on sys.path when this file is the subprocess entry point.
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from mdpack import DATE, FIX, NOW, SPEC, copy_pack, edit_json, edit_text, rehash  # noqa: E402
from test_marketdata_r7 import _fresh_contract, _jpm_event_time  # noqa: E402

from optpipe import mdfeeds, marketdata as md, nyse_calendar as cal  # noqa: E402
from optpipe.cli import main as cli_main  # noqa: E402
from optpipe.models.contracts import DiscoveryInput, Pick, ResearchInput  # noqa: E402
from optpipe.providers.global_snapshot import UnavailableGlobalSnapshot  # noqa: E402
from optpipe.providers.pack import PackMarket, pack_macro_from_catalysts  # noqa: E402
from optpipe.research.iv import IvHistoryStore  # noqa: E402
from optpipe.watcher.runtime import PackQuoteSource  # noqa: E402

NY = ZoneInfo("America/New_York")
ALLOW = ("MARKET", "EXAMPLE", "TEST")
RUN_ID = "run-r7-7"
CLOSURE = "none"
# Consumer quote gate matches test_pr1's direct PackMarket checks: hold_end is None,
# so R07/R08 are not what this receipt is measuring.
HOLD_END = None

FUTURE = datetime(2026, 10, 20, 8, 0, tzinfo=NY)
STALE_AT = datetime(2026, 10, 20, 9, 40, tzinfo=NY)
NAIVE = datetime(2026, 10, 20, 9, 5)
FAR = datetime(2028, 1, 5, 12, 0, tzinfo=NY)

# case, failure class, clock. positive_control is the non-empty twin.
CASES = (
    ("positive", "positive_control", NOW),
    ("missing", "missing_malformed", NOW),
    ("malformed", "missing_malformed", NOW),
    ("tamper", "missing_malformed", NOW),
    ("oversized", "missing_malformed", NOW),
    ("wrong_path", "missing_malformed", NOW),
    ("stale", "stale_future_lookahead", STALE_AT),
    ("future", "stale_future_lookahead", FUTURE),
    ("lookahead", "stale_future_lookahead", NOW),
    ("calendar", "calendar_clock", FAR),
    ("naive_clock", "calendar_clock", NAIVE),
)


def _ban_wall_clock() -> None:
    def boom(*_a, **_k):
        raise RuntimeError("replay read the wall clock")

    md._wall_clock = boom


def _shift_built_day(doc, day: str) -> None:
    """Same date shift as test_pr1_pack_consumer_precision._shift_built_day."""
    if isinstance(doc, dict):
        for k, v in list(doc.items()):
            if k in ("as_of", "pack_built_at") and isinstance(v, str) and v.startswith("2026-10-20T"):
                doc[k] = day + "T" + v[len("2026-10-20T"):]
            else:
                _shift_built_day(v, day)
    elif isinstance(doc, list):
        for v in doc:
            _shift_built_day(v, day)


def _place(src: Path, root: Path, day: str) -> Path:
    dest = root / day
    shutil.copytree(src, dest)
    for p in [dest, *dest.iterdir()]:
        if not p.is_symlink():
            p.chmod(0o755 if p.is_dir() else 0o644)
    for name in dest.glob("*.json"):
        doc = json.loads(name.read_text())
        _shift_built_day(doc, day)
        if name.name in ("index.json", "catalysts.json"):
            doc["date"] = day
        name.write_text(json.dumps(doc, indent=2) + "\n")
    rehash(dest)
    return dest


def _fresh(d: Path) -> None:
    edit_json(d, "JPM.json", _fresh_contract)
    edit_json(d, "catalysts.json", _jpm_event_time)


def _ids(errors) -> list[str]:
    return [e.split(" ", 1)[0] for e in errors]


def build_cases(work: Path) -> dict[str, tuple[Path, datetime]]:
    """Private labeled copies under work. Vendored fixtures and spec/ are only read."""
    built: dict[str, tuple[Path, datetime]] = {}
    for name, _cls, clock in CASES:
        d = copy_pack(work / "cases" / name)
        if name == "positive":
            _fresh(d)
        elif name == "missing":
            (d / "pack.sha256").unlink()
        elif name == "malformed":
            (d / "JPM.json").write_text("{")
            rehash(d)
        elif name == "tamper":
            edit_text(d, "JPM.json", '"value": 101.25', '"value": 1.0', hash_it=False)
        elif name == "oversized":
            p = d / "summary.md"
            p.write_bytes(p.read_bytes() + b"x" * (1024 * 1024 + 1))
            rehash(d)
        elif name == "wrong_path":
            man = d / "pack.sha256"
            man.write_text(man.read_text().rstrip("\n") + "\n" + ("0" * 64) + "  ../outside.json\n")
        elif name in ("stale", "lookahead"):
            _fresh(d)
        elif name == "future":
            pass
        elif name == "calendar":
            _place(d, d.parent, "2028-01-05")
        elif name == "naive_clock":
            pass
        else:
            raise AssertionError(name)
        built[name] = (d.parent, clock)
    return built


def _clock_text(clock: datetime) -> str:
    if clock.tzinfo is None:
        return "naive:" + clock.isoformat()
    return clock.isoformat()


def _cli(argv: list[str]) -> int:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return cli_main(argv)


def _market(res, clock: datetime, store=None) -> PackMarket:
    m = PackMarket(res, now=clock, hold_end=HOLD_END, allow_labels=ALLOW,
                   macro=pack_macro_from_catalysts(res, now=clock) if res.ok else [])
    m.iv_store = store
    return m


def _ahead(clock: datetime):
    last = cal.last_completed_session(clock)
    if last is None:
        raise RuntimeError("no last completed session")
    nxt = last + timedelta(days=1)
    while cal.is_session(nxt) is not True:
        nxt += timedelta(days=1)
        if nxt.year > 2027:
            raise RuntimeError("no later session")
    return nxt


def _discovery(m: PackMarket, clock: datetime) -> dict:
    from optpipe.discovery.agent import DiscoveryAgent
    from pydantic import ValidationError
    try:
        inp = DiscoveryInput(as_of=clock, run_id=RUN_ID, screens=["pack"], watchlist=[], max_candidates=8)
    except ValidationError:
        return {"invoked": False, "reject": "ValidationError", "screen_status": None,
                "pick_symbols": [], "jpm_last": None}
    agent = DiscoveryAgent(m, UnavailableGlobalSnapshot([]), "")
    disc = agent.run(inp)
    screen = m.get_screen("pack")
    last = None
    raw = disc.model_dump(mode="json")
    for sec in raw["premarket_scan"]["screens"]:
        for row in sec["rows"]:
            if row["ticker"] == "JPM":
                last = (row.get("last") or {}).get("value")
    return {"invoked": True, "reject": None, "screen_status": screen.status,
            "pick_symbols": [p.symbol for p in disc.picks], "jpm_last": last}


def _research(m: PackMarket, clock: datetime) -> dict:
    from optpipe.research.agent import ResearchAgent
    from pydantic import ValidationError
    try:
        inp = ResearchInput(as_of=clock, run_id=RUN_ID, picks=[Pick(symbol="JPM")], strike_window_pct=10)
    except ValidationError:
        return {"invoked": False, "reject": "ValidationError", "quote_status": None,
                "quote_value": None, "no_idea": False}
    agent = ResearchAgent(m, m, m, macro=m.macro_provider, iv_store=m.iv_store, data_mode="packs")
    out = agent.run(inp)
    raw = out.model_dump(mode="json")
    last = raw["candidates"][0]["snapshots"][0]["quote"]["last"]
    no_idea = any("NO IDEA" in n or "unavailable" in n for n in out.notes)
    return {"invoked": True, "reject": None, "quote_status": last["status"],
            "quote_value": last["value"], "no_idea": no_idea}


def _snap(root: Path, clock: datetime, allow) -> dict:
    src = PackQuoteSource(root, allow_labels=allow, spec=SPEC)
    try:
        snap = src.snapshot(clock, ["JPM"], [])
    except Exception as ex:  # noqa: BLE001 - record the actual failure, do not invent one
        return {"last_status": "raised", "last_value": None, "raised": type(ex).__name__,
                "method": src.method, "reasons": []}
    field = snap["underlyings"]["JPM"]["last"]
    market = src._market(clock)
    reasons = list(market.reasons("JPM")) if market is not None else []
    return {"last_status": field["status"], "last_value": field["value"], "raised": None,
            "method": src.method, "reasons": reasons}


def _watch_hash(out_dir: Path) -> str | None:
    files = sorted(out_dir.glob("*.watch.json"))
    if not files:
        return None
    return hashlib.sha256(files[0].read_bytes()).hexdigest()


def evaluate(name: str, failure_class: str, root: Path, clock: datetime, session: Path, work: Path) -> tuple[dict, dict]:
    store = None
    if name == "lookahead":
        store = IvHistoryStore(work / "iv" / name)
        store.record("JPM", _ahead(clock), 0.31, NOW, "hostile", "packs")
    if clock.tzinfo is None:
        res = mdfeeds.select_pack(root, clock, spec=SPEC)
        day = DATE
        now_arg = "2026-10-20T09:05:00"
    elif name == "calendar":
        res = mdfeeds.select_pack(root, clock, spec=SPEC)
        day = "2028-01-05"
        now_arg = clock.isoformat()
    else:
        res = mdfeeds.select_pack(root, clock, spec=SPEC)
        day = clock.astimezone(NY).date().isoformat()
        now_arg = clock.isoformat()
    if store is not None and res.ok:
        # select_pack does not carry the store; attach it on the market the consumers share.
        pass
    m = _market(res, clock, store)
    disc = _discovery(m, clock)
    research = _research(m, clock)
    allow_snap = _snap(root, clock, ALLOW)
    market_snap = _snap(root, clock, ("MARKET",))
    reasons = []
    if clock.tzinfo is not None and res.ok:
        reasons = m.reasons("JPM", hold_end=HOLD_END)
    watch_out = work / "watch" / name
    watch_out.mkdir(parents=True, exist_ok=True)
    watch_rc = _cli(["watch", "--session", str(session), "--data-dir", str(root),
                     "--clock", now_arg, "--once", "--out", str(watch_out)])
    md_rc = _cli(["market-data", "--data-dir", str(root), "--date", day, "--now", now_arg])
    raw = {
        "closure_claim": CLOSURE,
        "case": name,
        "failure_class": failure_class,
        "clock": _clock_text(clock),
        "hold_end": None,
        "pack": {
            "ok": bool(res.ok),
            "loader_exit": res.exit_code(),
            "market_data_cli_exit": md_rc,
            "error_ids": _ids(res.errors),
            "errors": list(res.errors),
            "decision_reasons": list(reasons),
        },
        "discovery": disc,
        "research": research,
        "watcher": {
            "allow_example": allow_snap,
            "market_only": market_snap,
            "watch_cli_exit": watch_rc,
            # Present/absent only. The watch.json bytes embed the fixture session digest, and that
            # session's trace carries stage durations, so the hash is not a frozen raw projection.
            # The parent test still compares the watch.json bytes across the matrix.
            "watch_json_present": _watch_hash(watch_out) is not None,
        },
    }
    semantic = {
        "closure_claim": CLOSURE,
        "normalization": "id_status_v1",
        "case": name,
        "failure_class": failure_class,
        "clock": _clock_text(clock),
        "pack_ok": bool(res.ok),
        "loader_exit": res.exit_code(),
        "market_data_cli_exit": md_rc,
        "error_ids": sorted(set(_ids(res.errors))),
        "decision_ids": sorted({e.split(" ", 1)[0] for e in reasons}),
        "discovery_invoked": disc["invoked"],
        "discovery_reject": disc["reject"],
        "discovery_screen_status": disc["screen_status"],
        "discovery_picks": sorted(disc["pick_symbols"]),
        "discovery_jpm_last": disc["jpm_last"],
        "research_invoked": research["invoked"],
        "research_reject": research["reject"],
        "research_quote_status": research["quote_status"],
        "research_quote_value": research["quote_value"],
        "research_no_idea": research["no_idea"],
        "watcher_allow_example_status": allow_snap["last_status"],
        "watcher_allow_example_value": allow_snap["last_value"],
        "watcher_allow_example_reason_ids": sorted({e.split(" ", 1)[0] for e in allow_snap["reasons"]}),
        "watcher_market_only_status": market_snap["last_status"],
        "watcher_market_only_value": market_snap["last_value"],
        "watcher_market_only_reason_ids": sorted({e.split(" ", 1)[0] for e in market_snap["reasons"]}),
        "watch_cli_exit": watch_rc,
        "watch_json_present": raw["watcher"]["watch_json_present"],
    }
    return raw, semantic


def _dump_raw(obj: dict) -> bytes:
    return (json.dumps(obj, indent=2) + "\n").encode()


def _dump_semantic(obj: dict) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


def run_cell(work: Path, session: Path) -> None:
    if hasattr(time, "tzset"):
        time.tzset()
    _ban_wall_clock()
    work.mkdir(parents=True, exist_ok=True)
    raw_dir = work / "raw"
    sem_dir = work / "semantic"
    raw_dir.mkdir()
    sem_dir.mkdir()
    built = build_cases(work)
    for name, failure_class, clock in CASES:
        root, _clock = built[name]
        assert _clock is clock
        raw, semantic = evaluate(name, failure_class, root, clock, session, work)
        (raw_dir / f"{name}.json").write_bytes(_dump_raw(raw))
        (sem_dir / f"{name}.json").write_bytes(_dump_semantic(semantic))
    cell = {
        "closure_claim": CLOSURE,
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "tz": os.environ.get("TZ"),
        "optpipe_netguard": os.environ.get("OPTPIPE_NETGUARD"),
        "pythondontwritebytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"),
        "python": sys.version,
        "argv0_name": Path(sys.argv[0]).name,
        "argv_rest": sys.argv[1:],
    }
    (work / "cell.json").write_text(json.dumps(cell, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R7 item 7 failure replay cell")
    ap.add_argument("--work", required=True)
    ap.add_argument("--session", required=True)
    args = ap.parse_args(argv)
    run_cell(Path(args.work), Path(args.session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
