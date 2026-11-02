"""M3 watcher runtime: session -> targets (vetoed flagged / hypothetical), NY-hours gating, every rule
through the runtime, watch.json + sha256 sidecar, GET-only quotes, no outside contact."""
from __future__ import annotations

import ast
import copy
import json
import socket
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from optpipe.risk.engine import Calendar
from optpipe.risk.ruleset import MarketHours
from optpipe.watcher import runtime as rt
from optpipe.watcher.runtime import (ReplayTape, SimClock, Watcher, market_state, register_engine, targets_from_session,
                                     validate_watch, verify_sidecar, write_watch)

NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
TAPE = ROOT / "tests" / "fixtures" / "2026-10-13" / "watch_tape.yaml"


def ny(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=NY)


WATCH_NOW = ny("2026-10-13T12:30:00")      # targets_from_session needs an explicit watch start (FIX-SEC-001 N7)


@pytest.fixture(scope="module")
def cal(raw_rules):
    return Calendar(MarketHours.model_validate(raw_rules["config"]["market_hours"]))


@pytest.fixture(scope="module")
def wrules(spec):
    return spec.watcher_rules()


def hypothetical(rec: dict) -> dict:
    """The fixture tickets with the VETO removed and no approval: HYPOTHETICAL paper what-ifs. The reviews are
    honest PASS reviews (results and overall agree), since a stored PASS over vetoing results is VETO (INV-16)."""
    r = copy.deepcopy(rec)
    for rv in r["risk_reviews"]:
        for x in rv["results"]:
            x["result"] = "PASS"
        rv["overall"].update(verdict="PASS", blocking_rule_ids=[], flag_rule_ids=[])
    for i in r["idea_log"]:
        i["current_status"] = "proposed"
    return r


class DictSource:
    """In-memory GET-only source: quotes(now) -> dict; records every call."""
    name, method = "dict", "GET"

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def snapshot(self, now, symbols, contracts):
        self.calls.append({"method": "GET", "at": now.isoformat(), "symbols": list(symbols)})
        return self.fn(now, symbols, contracts)


def q(v, at):
    return {"value": v, "status": "ok", "as_of": at.isoformat(), "source_id": "src-test"}


def quotes(now, under: dict, opts: dict, bars=None, as_of=None):
    a = as_of or now
    return {"underlyings": {s: {"last": q(v, a), "bars_5m": (bars or {}).get(s, [])} for s, v in under.items()},
            "options": {c: {"bid": q(b, a), "ask": q(k, a), "iv": q(0.25, a)} for c, (b, k) in opts.items()}}


JPM_BCS = "tkt-jpm-bcs-340-350"
C340, C350 = "JPM261030C00340000", "JPM261030C00350000"


def only_jpm_bcs(rec):
    r = hypothetical(rec)
    r["tickets"] = [t for t in r["tickets"] if t["ticket_id"] == JPM_BCS]
    return r


def run_at(rec, wrules, cal, times, fn):
    src = DictSource(fn)
    w = Watcher(rec, wrules, cal, src, now=times[0])
    for t in times:
        w.step(t)
    return w, src


def rules_hit(w):
    return [(a["rule_id"], a["level"]) for a in w.alerts]


# ------------------------------------------------------------------ targets
def test_vetoed_tickets_are_flagged_shadow(rec):
    ts = targets_from_session(rec, WATCH_NOW)
    assert ts and all(t.vetoed and t.status == "vetoed" and t.shadow and not t.hypothetical for t in ts)
    assert all("VETOED" in t.note and "R0" in t.note for t in ts)


def test_hypothetical_rejected_approved_and_positions(rec):
    r = hypothetical(rec)
    ts = targets_from_session(r, WATCH_NOW)
    assert all(t.hypothetical and t.status == "approved_pending" and not t.vetoed for t in ts)
    r["idea_log"][1]["current_status"] = "rejected"
    # A schema-shaped approval (status `active`, inside its window, every flag acknowledged) of an honest PASS
    # review. The old non-schema {"decision": "approved"} no longer counts (FIX-SEC-001 P0-4, test_sec001_watcher.py).
    t2 = r["tickets"][2]["ticket_id"]
    rv2 = next(x for x in reversed(r["risk_reviews"]) if x["ticket_id"] == t2)
    for x in rv2["results"]:
        x["result"] = "PASS"
    rv2["overall"].update(verdict="PASS", blocking_rule_ids=[], flag_rule_ids=[])
    now = ny("2026-10-13T12:30:00")
    tk2 = r["tickets"][2]                     # INV-05-TERMS (1.1.3): the approval carries the ticket's exact terms
    r["approvals"] = [{"approval_id": "apr-t2", "ticket_id": t2, "review_id": rv2["review_id"], "status": "active",
                       "acknowledged_flags": [], "approved_at": rt.pair(ny("2026-10-13T12:20:00")),
                       "expires_at": rt.pair(ny("2026-10-13T16:00:00")),
                       "legs": copy.deepcopy(tk2["legs"]), "quantity": tk2["quantity"], "limit_price": tk2["limit_price"]}]
    r["paper_positions"] = [{"position_id": "pos-1", "ticket_id": r["tickets"][3]["ticket_id"], "status": "open"}]
    by = {(t.target_id, t.kind): t for t in targets_from_session(r, now)}
    assert by[(r["tickets"][1]["ticket_id"], "ticket")].status == "rejected"
    appr = by[(r["tickets"][2]["ticket_id"], "ticket")]
    assert appr.status == "approved_pending" and not appr.hypothetical
    assert by[("pos-1", "position")].status == "open"


def test_one_vetoed_among_hypothetical_stays_vetoed(rec):
    r = hypothetical(rec)
    r["risk_reviews"][-1]["overall"]["verdict"] = "VETO"
    vid = r["risk_reviews"][-1]["ticket_id"]
    t = {x.ticket_id: x for x in targets_from_session(r, WATCH_NOW)}
    assert t[vid].vetoed and t[vid].shadow and sum(x.vetoed for x in t.values()) == 1


# ------------------------------------------------------------------ gating
@pytest.mark.parametrize("t,state", [
    ("2026-10-13T08:00:00", (False, "premarket")),
    ("2026-10-13T09:29:59", (False, "premarket")),
    ("2026-10-13T09:30:00", (True, "open")),
    ("2026-10-13T15:59:59", (True, "open")),
    ("2026-10-13T16:00:00", (False, "after_close")),
    ("2026-10-17T11:00:00", (False, "weekend")),
    ("2026-11-26T11:00:00", (False, "holiday")),        # Thanksgiving
    ("2026-11-27T12:59:00", (True, "open")),            # early close day
    ("2026-11-27T13:00:00", (False, "early_close")),
    ("2026-12-24T14:00:00", (False, "early_close")),
])
def test_market_state(cal, t, state):
    assert market_state(ny(t), cal) == state


def _count_run(rec, wrules, cal, start, until):
    src = DictSource(lambda now, s, c: quotes(now, {"JPM": 336.0, "WDC": 363.0},
                                              {cid: (1.0, 1.1) for cid in c}))
    w = Watcher(hypothetical(rec), wrules, cal, src, now=ny(start))
    doc = w.run(SimClock(ny(start)), ny(until))
    return doc, src


@pytest.mark.parametrize("start,until,why", [
    ("2026-10-13T04:00:00", "2026-10-13T09:29:00", "premarket"),
    ("2026-10-13T16:00:00", "2026-10-13T23:59:00", "after_close"),
    ("2026-10-17T00:00:00", "2026-10-18T23:59:00", "weekend"),
    ("2026-11-26T00:00:00", "2026-11-26T23:59:00", "holiday"),
    ("2026-11-27T13:00:00", "2026-11-27T23:59:00", "early_close"),
])
def test_no_quote_calls_while_closed(rec, wrules, cal, start, until, why):
    doc, src = _count_run(rec, wrules, cal, start, until)
    assert src.calls == [] and doc["summary"]["quote_calls"] == 0
    assert doc["wakes"] and all(w["market"] == why and w["quote_calls"] == 0 and "no quote request" in w["gated"]
                                for w in doc["wakes"])
    assert not any(a["rule_id"].startswith("W") for a in doc["alerts"])
    assert validate_watch(doc) == []


def test_early_close_polls_stop_at_13_00(rec, wrules, cal):
    doc, src = _count_run(rec, wrules, cal, "2026-11-27T09:00:00", "2026-11-27T18:00:00")
    times = [datetime.fromisoformat(c["at"]).astimezone(NY) for c in src.calls]
    assert times and min(times) >= ny("2026-11-27T09:30:00") and max(times) < ny("2026-11-27T13:00:00")
    assert any(w["market"] == "early_close" for w in doc["wakes"])
    assert validate_watch(doc) == []


def test_closed_jumps_to_next_open(cal, rec, wrules):
    w = Watcher(hypothetical(rec), wrules, cal, DictSource(lambda *a: {}), now=ny("2026-10-16T17:00:00"))
    assert w.step(ny("2026-10-16T17:00:00")) == ny("2026-10-19T09:30:00")      # Fri -> Mon
    assert w.step(ny("2026-11-25T16:30:00")) == ny("2026-11-27T09:30:00")      # skips Thanksgiving


def test_full_replay_day_hypothetical(rec, wrules, cal):
    src = ReplayTape(TAPE)
    doc = Watcher(hypothetical(rec), wrules, cal, src, now=ny("2026-10-13T07:00:00")).run(SimClock(ny("2026-10-13T07:00:00")), ny("2026-10-13T17:00:00"))
    assert validate_watch(doc) == []
    calls = [datetime.fromisoformat(c["at"]).astimezone(NY) for c in src.calls]
    assert min(calls) == ny("2026-10-13T09:30:00") and max(calls) < ny("2026-10-13T16:00:00")
    assert all(c["method"] == "GET" for c in src.calls)
    assert all(a["hypothetical"] and not a["vetoed"] for a in doc["alerts"])
    rules = {a["rule_id"] for a in doc["alerts"]}
    assert "W07_EVENT_EVE" in rules        # CPI 2026-10-14 08:30 is the next session
    assert "W03_IV_VS_ENTRY" in rules      # entry IV not in the record -> UNKNOWN info
    gap = [a for a in doc["alerts"] if a["rule_id"] == "W06_DATA_HEALTH"]
    assert gap and all(ny("2026-10-13T12:20:00") <= datetime.fromisoformat(a["at"]["ny"]) <= ny("2026-10-13T12:30:00")
                       for a in gap[:1])
    assert all(a["message"].endswith("alert only, no order") for a in doc["alerts"])


# ------------------------------------------------------------------ each rule through the runtime
T10 = ny("2026-10-14T10:30:00")


def test_rule_w01_mark_at_stop(rec, wrules, cal):
    w, _ = run_at(only_jpm_bcs(rec), wrules, cal, [T10],
                  lambda n, s, c: quotes(n, {"JPM": 333.0}, {C340: (2.50, 2.60), C350: (1.05, 1.10)}))
    assert ("W01_MARK_VS_STOP_TARGET", "exit_signal") in rules_hit(w)      # mark 1.475 <= stop 1.52


def test_rule_w01_near_target_warns(rec, wrules, cal):
    w, _ = run_at(only_jpm_bcs(rec), wrules, cal, [T10],
                  lambda n, s, c: quotes(n, {"JPM": 347.0}, {C340: (9.60, 9.70), C350: (3.80, 3.90)}))
    assert ("W01_MARK_VS_STOP_TARGET", "warn") in rules_hit(w)             # 5.80 within 10% of 6.05


def test_rule_w02_stop_cross_and_5min_close(rec, wrules, cal):
    t1, t2 = T10, ny("2026-10-14T10:35:00")
    seq = {t1: 331.0, t2: 329.5}
    def fn(n, s, c):
        return quotes(n, {"JPM": seq[n]}, {C340: (3.0, 3.1), C350: (1.0, 1.05)},
                      bars={"JPM": [{"end": n.isoformat(), "close": seq[n]}]})
    w, _ = run_at(only_jpm_bcs(rec), wrules, cal, [t1, t2], fn)
    assert ("W02_UNDERLYING_LEVELS", "exit_signal") in rules_hit(w)


def test_rule_w03_unknown_entry_iv(rec, wrules, cal):
    w, _ = run_at(only_jpm_bcs(rec), wrules, cal, [T10],
                  lambda n, s, c: quotes(n, {"JPM": 337.0}, {C340: (5.0, 5.1), C350: (2.0, 2.05)}))
    a = [x for x in w.alerts if x["rule_id"] == "W03_IV_VS_ENTRY"]
    assert a and a[0]["level"] == "info" and "UNKNOWN" in a[0]["detail"]


def test_rule_w03_iv_crush_with_entry_iv(rec, wrules, cal):
    r = only_jpm_bcs(rec)
    for l in r["tickets"][0]["legs"]:
        l["entry_iv"] = 0.40
    w, _ = run_at(r, wrules, cal, [T10],
                  lambda n, s, c: quotes(n, {"JPM": 337.0}, {C340: (5.0, 5.1), C350: (2.0, 2.05)}))
    assert ("W03_IV_VS_ENTRY", "exit_signal") in rules_hit(w)             # 0.25 / 0.40 - 1 = -37.5%


def test_rule_w04_time_stop(rec, wrules, cal):
    w, _ = run_at(only_jpm_bcs(rec), wrules, cal, [ny("2026-10-27T10:05:00")],
                  lambda n, s, c: quotes(n, {"JPM": 337.0}, {C340: (3.0, 3.1), C350: (0.5, 0.55)}))
    assert ("W04_TIME", "exit_signal") in rules_hit(w)


def test_rule_w05_short_call_itm_low_extrinsic(rec, wrules, cal):
    w, _ = run_at(only_jpm_bcs(rec), wrules, cal, [ny("2026-10-26T11:00:00")],
                  lambda n, s, c: quotes(n, {"JPM": 355.0}, {C340: (15.0, 15.1), C350: (5.00, 5.04)}))
    assert ("W05_SHORT_LEG_ASSIGNMENT", "exit_signal") in rules_hit(w)    # extrinsic 0.02 < 0.05


def test_rule_w06_stale_quotes_suppress_exit(rec, wrules, cal):
    old = ny("2026-10-14T10:00:00")
    w, _ = run_at(only_jpm_bcs(rec), wrules, cal, [T10],
                  lambda n, s, c: quotes(n, {"JPM": 333.0}, {C340: (2.50, 2.60), C350: (1.05, 1.10)}, as_of=old))
    hits = rules_hit(w)
    assert ("W06_DATA_HEALTH", "warn") in hits and not any(l == "exit_signal" for _, l in hits)


def test_rule_w07_event_eve(rec, wrules, cal):
    w, _ = run_at(only_jpm_bcs(rec), wrules, cal, [ny("2026-10-13T11:00:00")],
                  lambda n, s, c: quotes(n, {"JPM": 337.0}, {C340: (5.0, 5.1), C350: (2.0, 2.05)}))
    a = [x for x in w.alerts if x["rule_id"] == "W07_EVENT_EVE"]
    assert a and "ev-cpi-2026-10-14" in a[0]["detail"]


def test_shadow_eod_for_vetoed_only_after_close(rec, wrules, cal):
    src = ReplayTape(TAPE)
    w = Watcher(rec, wrules, cal, src, now=ny("2026-10-13T07:00:00"))
    doc = w.run(SimClock(ny("2026-10-13T07:00:00")), ny("2026-10-13T17:00:00"))
    assert len(src.calls) == 1 and src.calls[0]["at"].startswith("2026-10-13T15:55")
    assert [a["rule_id"] for a in doc["alerts"]] == ["SHADOW_EOD"] * len(rec["tickets"])
    assert all(a["vetoed"] and a["target_status"] == "vetoed" and a["at"]["ny"].startswith("2026-10-13T16:00")
               for a in doc["alerts"])
    assert all("would-be mark" in a["detail"] for a in doc["alerts"])
    assert doc["summary"]["vetoed"] == len(rec["tickets"]) and doc["summary"]["watched"] == 0
    assert validate_watch(doc) == []


# ------------------------------------------------------------------ output
def test_watch_json_and_sidecar(rec, wrules, cal, tmp_path):
    sess = tmp_path / "session-2026-10-13.json"
    sess.write_text(json.dumps(rec))
    w = Watcher(rec, wrules, cal, ReplayTape(TAPE), session_path=sess, now=ny("2026-10-13T07:00:00"))
    doc = w.run(SimClock(ny("2026-10-13T07:00:00")), ny("2026-10-13T17:00:00"))
    res = write_watch(doc, sess)
    assert res.json_path.name == "session-2026-10-13.watch.json"
    side = Path(str(res.json_path) + ".sha256")
    assert side.read_text() == f"{res.sha256}  session-2026-10-13.watch.json\n"
    assert verify_sidecar(res.json_path)
    d = json.loads(res.json_path.read_text())
    assert d["label"].startswith("PAPER ONLY") and d["session"]["sha256"] and d["mode"]["quote_method"] == "GET"
    assert d["generated_at"]["berlin"].endswith("+02:00")
    res.json_path.write_text(res.json_path.read_text().replace("SHADOW_EOD", "SHADOW_EOX"))
    assert not verify_sidecar(res.json_path)


def test_validate_watch_catches_violations(rec, wrules, cal):
    doc = Watcher(rec, wrules, cal, ReplayTape(TAPE), now=ny("2026-10-13T07:00:00")).run(SimClock(ny("2026-10-13T07:00:00")), ny("2026-10-13T17:00:00"))
    bad = copy.deepcopy(doc)
    bad["wakes"][0]["quote_calls"] = 1                       # 07:00 premarket
    bad["alerts"][0]["rule_id"] = "W01_MARK_VS_STOP_TARGET"  # shadow target alerted intraday
    errs = validate_watch(bad)
    assert any("while market premarket" in e for e in errs) and any("intraday alert" in e for e in errs)


def test_pluggable_engine(rec, wrules, cal):
    class Quiet:
        name = "quiet"
        seen = 0

        def poll(self, inp, state, rules, c):
            Quiet.seen += 1
            return [], state or {}, 300
    register_engine("quiet", Quiet)
    doc = Watcher(hypothetical(rec), wrules, cal, ReplayTape(TAPE), rt.get_engine("quiet"), now=ny("2026-10-13T09:30:00")).run(
        SimClock(ny("2026-10-13T09:30:00")), ny("2026-10-13T10:00:00"))
    assert Quiet.seen > 0 and doc["alerts"] == [] and doc["engine"] == "quiet"
    with pytest.raises(KeyError):
        rt.get_engine("nope")


# ------------------------------------------------------------------ CLI + no outside contact
def test_cli_fixture_run_validates_with_no_network(pipeline_output, tmp_path, monkeypatch, capsys):
    from optpipe.cli import main

    def boom(*a, **k):
        raise AssertionError("network contact attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    import requests
    for m in ("get", "post", "put", "patch", "delete", "request"):
        monkeypatch.setattr(requests, m, boom)
    sess = pipeline_output["out"] / "session-2026-10-13.json"
    rc = main(["watch", "--session", str(sess), "--replay", str(TAPE), "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "validate_watch: OK" in out and "sidecar OK" in out and "PAPER ONLY" in out
    assert (tmp_path / "session-2026-10-13.watch.json").exists()
    assert (tmp_path / "session-2026-10-13.watch.json.sha256").exists()


def test_cli_once_and_arg_checks(pipeline_output, tmp_path, capsys):
    from optpipe.cli import main
    sess = str(pipeline_output["out"] / "session-2026-10-13.json")
    assert main(["watch", "--session", sess, "--out", str(tmp_path)]) == 2
    assert main(["watch", "--session", sess, "--live", "--clock", "2026-10-13T10:00:00-04:00"]) == 2
    rc = main(["watch", "--session", sess, "--replay", str(TAPE), "--clock", "2026-10-13T08:00:00-04:00", "--once",
               "--out", str(tmp_path)])
    assert rc == 0
    d = json.loads((tmp_path / "session-2026-10-13.watch.json").read_text())
    assert len(d["wakes"]) == 1 and d["wakes"][0]["market"] == "premarket" and d["summary"]["quote_calls"] == 0


def test_watcher_package_is_alert_only_and_get_only():
    pkg = ROOT / "src" / "optpipe" / "watcher"
    for p in pkg.glob("*.py"):
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in n.names] + ([n.module] if isinstance(n, ast.ImportFrom) and n.module else [])
                assert not any(m and m.split(".")[0] in {"smtplib", "socket", "http", "urllib", "requests", "httpx",
                                                         "aiohttp", "slack_sdk", "twilio", "telegram", "ib_insync",
                                                         "alpaca_trade_api"} for m in mods), (p.name, mods)
            if isinstance(n, ast.Attribute):
                assert n.attr not in {"post", "put", "patch", "delete", "place_order", "submit_order", "send_message",
                                      "sendmail"}, (p.name, n.attr)
            if isinstance(n, ast.FunctionDef):
                assert not any(w in n.name.lower() for w in ("order", "execute", "notify", "send")), (p.name, n.name)
    for cls in (rt.ReplayTape, rt.LiveNasdaqSource):
        assert cls.method == "GET"
