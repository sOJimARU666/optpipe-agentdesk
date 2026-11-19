"""Slice 1 (D01–D06) on the pinned 1.1.8 watcher and validators. Paper only, no network.

Proposed ids from the D01–D06 behaviour change. Both spec/watcher_reference.poll and
optpipe.watcher.engine.poll are checked, because the two must keep agreeing.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from optpipe.risk.engine import Calendar
from optpipe.risk.ruleset import MarketHours
from optpipe.watcher import engine as eng

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec"
PY = sys.executable


@pytest.fixture(scope="module")
def wref(spec):
    return spec.load_module("watcher_reference")


@pytest.fixture(scope="module")
def rules(spec):
    return spec.watcher_rules()


@pytest.fixture(scope="module")
def cal(raw_rules):
    return Calendar(MarketHours.model_validate(raw_rules["config"]["market_hours"]))


def _levels(alerts):
    return [[a["rule_id"], a["level"]] for a in alerts]


def _details(alerts):
    return " | ".join(a.get("detail") or "" for a in alerts)


def _both(wref, rules, cal, inp, state=None):
    """Run the reference and the engine. A raised poll is a failure of the contract, not a pass."""
    out = []
    for name, fn in (
        ("reference", lambda: wref.poll(copy.deepcopy(inp), copy.deepcopy(state), rules)),
        ("engine", lambda: eng.poll(copy.deepcopy(inp), copy.deepcopy(state), rules, cal)),
    ):
        try:
            alerts, new_state, nxt = fn()
        except Exception as ex:  # noqa: BLE001 - the contract is that poll does not raise
            pytest.fail(f"{name} poll raised {type(ex).__name__}: {ex}")
        out.append((alerts, new_state, nxt))
    ref_a, eng_a = out
    assert _levels(ref_a[0]) == _levels(eng_a[0])
    assert ref_a[2] == eng_a[2]
    assert _details(ref_a[0]) == _details(eng_a[0])
    for key in ("session_date", "sent", "last_at", "wide_streak"):
        assert ref_a[1].get(key) == eng_a[1].get(key), key
    return ref_a


def _case(ref, rules, **set_):
    return ref.apply_case(rules["fixture"], {"set": set_})


def _quotes(day, hhmm, off):
    stamp = f"{day}T{hhmm}:00{off}"
    return {
        "underlying.last.as_of": stamp,
        "legs.0.bid.as_of": stamp,
        "legs.0.ask.as_of": stamp,
        "legs.1.bid.as_of": stamp,
        "legs.1.ask.as_of": stamp,
        "underlying.bars_5m": [],
    }


def _expiry(day, hhmm, off, as_of_hhmm, **extra):
    data = {
        "now": f"{day}T{hhmm}:00{off}",
        "time_stop": None,
        "legs.0.expiry": day,
        "legs.1.expiry": day,
        **_quotes(day, as_of_hhmm, off),
    }
    data.update(extra)
    return data


BREACH = {
    "legs.0.bid.value": 3.55,
    "legs.0.ask.value": 3.65,
    "legs.1.bid.value": 1.50,
    "legs.1.ask.value": 1.50,
}


def test_W04_g_early_close_expiry_exit_at_1100(ref, wref, rules, cal):
    """W04-g: 2026-11-27 11:00 NY (close 13:00 minus 120 min) is an expiry exit while the session is open."""
    inp = _case(ref, rules, **_expiry("2026-11-27", "11:00", "-05:00", "10:59"))
    alerts, _state, nxt = _both(wref, rules, cal, inp)
    assert _levels(alerts) == [["W04_TIME", "exit_signal"]]
    assert nxt is not None


def test_W04_h_early_close_boundary(ref, wref, rules, cal):
    """W04-h: 2026-12-24 10:59 NY is not yet the expiry exit; 11:00 is."""
    before = _case(ref, rules, **_expiry("2026-12-24", "10:59", "-05:00", "10:58"))
    alerts, _state, nxt = _both(wref, rules, cal, before)
    assert ["W04_TIME", "exit_signal"] not in _levels(alerts)
    assert ["W04_TIME", "warn"] in _levels(alerts)
    assert nxt is not None
    after = _case(ref, rules, **_expiry("2026-12-24", "11:00", "-05:00", "10:59"))
    alerts, _state, nxt = _both(wref, rules, cal, after)
    assert _levels(alerts) == [["W04_TIME", "exit_signal"]]
    assert nxt is not None


def test_W04_i_regular_expiry_still_1400(ref, wref, rules, cal):
    """W04-i: a regular 16:00 close still exits at 14:00 (same instant as W04-e)."""
    inp = _case(ref, rules, **_expiry("2026-11-20", "14:00", "-05:00", "13:59"))
    alerts, _state, nxt = _both(wref, rules, cal, inp)
    assert _levels(alerts) == [["W04_TIME", "exit_signal"]]
    assert nxt is not None


def test_early_close_time_stop_still_fires_at_1000(ref, wref, rules, cal):
    """Time-stop 10:00 is unchanged on an early-close expiry day."""
    inp = _case(ref, rules, **_expiry(
        "2026-11-27", "10:00", "-05:00", "09:59",
        time_stop="2026-11-27T13:00:00-05:00",
    ))
    alerts, _state, _nxt = _both(wref, rules, cal, inp)
    assert ["W04_TIME", "exit_signal"] in _levels(alerts)


def _stale_day(day, off, stale=True):
    data = {
        "now": f"{day}T11:00:00{off}",
        **_quotes(day, "10:59", off),
    }
    if stale:
        data["legs.1.ask.as_of"] = f"{day}T10:30:00{off}"
    return data


def test_DD_f_session_reset_refires_w06_next_day(ref, wref, rules, cal):
    """DD-f: a W06 warn on day D does not mute a new stale episode on the next trading day."""
    day1 = _case(ref, rules, **_stale_day("2026-10-20", "-04:00", True))
    a1, st, _n = _both(wref, rules, cal, day1)
    assert ["W06_DATA_HEALTH", "warn"] in _levels(a1)
    fresh = _case(ref, rules, **_stale_day("2026-10-20", "-04:00", False))
    a2, st, _n = _both(wref, rules, cal, fresh, st)
    assert ["W06_DATA_HEALTH", "warn"] not in _levels(a2)
    day2 = _case(ref, rules, **_stale_day("2026-10-21", "-04:00", True))
    a3, st, _n = _both(wref, rules, cal, day2, st)
    assert ["W06_DATA_HEALTH", "warn"] in _levels(a3)
    assert st.get("session_date") == "2026-10-21"


def test_DD_f_wide_streak_resets_with_the_session(ref, wref, rules, cal):
    """wide_streak is intraday: a streak carried from yesterday does not count on the new session."""
    inp = _case(ref, rules, **_stale_day("2026-10-21", "-04:00", False))
    inp["legs"][1]["bid"]["value"] = 2.00
    inp["legs"][1]["ask"]["value"] = 3.10
    state = {"session_date": "2026-10-20", "wide_streak": {"leg-2": 9}}
    _alerts, st, _n = _both(wref, rules, cal, inp, state)
    assert st["wide_streak"]["leg-2"] == 1


def test_DD_g_same_day_w06_still_suppressed(ref, wref, rules, cal):
    """DD-g: escalate-only dedupe inside one NY session is unchanged."""
    inp = _case(ref, rules, **_stale_day("2026-10-20", "-04:00", True))
    state = {
        "session_date": "2026-10-20",
        "sent": {"W06_DATA_HEALTH|pos-1": "warn"},
        "last_at": {"W06_DATA_HEALTH|pos-1": "2026-10-20T10:40:00-04:00"},
    }
    alerts, _st, _n = _both(wref, rules, cal, inp, state)
    assert ["W06_DATA_HEALTH", "warn"] not in _levels(alerts)


def test_W06_e_explicit_session_date_resets(ref, wref, rules, cal):
    """W06-e: state.session_date from the previous NY date clears non-shadow dedupe keys."""
    inp = _case(ref, rules, **_stale_day("2026-10-21", "-04:00", True))
    state = {
        "session_date": "2026-10-20",
        "sent": {
            "W06_DATA_HEALTH|pos-1": "warn",
            "SHADOW_EOD|tk-1|2026-10-20": "info",
        },
        "last_at": {"W06_DATA_HEALTH|pos-1": "2026-10-20T11:00:00-04:00"},
    }
    alerts, st, _n = _both(wref, rules, cal, inp, state)
    assert ["W06_DATA_HEALTH", "warn"] in _levels(alerts)
    assert st["sent"].get("SHADOW_EOD|tk-1|2026-10-20") == "info"
    assert st["session_date"] == "2026-10-21"


@pytest.mark.parametrize("mutate", ["naive", "garbage", "missing"])
def test_POLL_e_bad_now_does_not_raise(ref, wref, rules, cal, mutate):
    """POLL-e: missing, naive, or unparsable now returns no alerts and does not raise."""
    inp = _case(ref, rules, **BREACH)
    state = {"sent": {"W01_MARK_VS_STOP_TARGET|pos-1": "warn"}, "wide_streak": {"leg-1": 2}}
    if mutate == "naive":
        inp["now"] = "2026-10-20T11:00:00"
    elif mutate == "garbage":
        inp["now"] = "not-a-time"
    else:
        del inp["now"]
    alerts, new_state, nxt = _both(wref, rules, cal, inp, state)
    assert alerts == [] and nxt is None
    assert new_state == state


def test_ISO_a_missing_entry_isolates_w01(ref, wref, rules, cal):
    """ISO-a: missing mark_levels.entry does not abort the poll; W06 carries rule_error for W01."""
    inp = _case(ref, rules, **BREACH)
    del inp["mark_levels"]["entry"]
    alerts, _st, nxt = _both(wref, rules, cal, inp)
    assert nxt is not None
    assert ["W06_DATA_HEALTH", "warn"] in _levels(alerts)
    assert "rule_error:W01_MARK_VS_STOP_TARGET:KeyError" in _details(alerts)


def test_ISO_b_missing_hold_window_isolates_w07(ref, wref, rules, cal):
    """ISO-b: missing hold_window omits W07; W01 still evaluates."""
    inp = _case(ref, rules, **BREACH)
    del inp["hold_window"]
    alerts, _st, _n = _both(wref, rules, cal, inp)
    assert ["W01_MARK_VS_STOP_TARGET", "exit_signal"] in _levels(alerts)
    assert ["W07_EVENT_EVE", "info"] not in _levels(alerts)
    assert "rule_error:W07_EVENT_EVE:KeyError" in _details(alerts)


def test_ISO_c_zero_entry_iv_isolates_w03(ref, wref, rules, cal):
    """ISO-c: entry_iv 0 fails W03 only; the poll completes and W01 still exits."""
    inp = _case(ref, rules, **BREACH)
    inp["legs"][0]["entry_iv"] = 0
    alerts, _st, _n = _both(wref, rules, cal, inp)
    assert ["W01_MARK_VS_STOP_TARGET", "exit_signal"] in _levels(alerts)
    assert "rule_error:W03_IV_VS_ENTRY:DivisionByZero" in _details(alerts)


def test_ISO_d_one_rule_runtimeerror_does_not_block_siblings(ref, wref, rules, cal, monkeypatch):
    """ISO-d: a rule that raises RuntimeError contributes a W06 rule_error; siblings still alert."""
    def boom(_w):
        raise RuntimeError("iso-d")

    monkeypatch.setattr(wref, "w02", boom)
    monkeypatch.setitem(eng.QUOTE_RULES, "W02_UNDERLYING_LEVELS", boom)
    inp = _case(ref, rules, **BREACH)
    alerts, _st, _n = _both(wref, rules, cal, inp)
    assert ["W01_MARK_VS_STOP_TARGET", "exit_signal"] in _levels(alerts)
    assert "rule_error:W02_UNDERLYING_LEVELS:RuntimeError" in _details(alerts)


def test_W06_f_stale_underlying_keeps_w04_exit(ref, wref, rules, cal):
    """W06-f: stale underlying.last on a regular expiry at 14:00 still sends the W04 exit and a W06 warn."""
    inp = _case(ref, rules, **_expiry(
        "2026-10-30", "14:00", "-04:00", "13:59",
        **{"underlying.last.as_of": "2026-10-30T13:30:00-04:00"},
    ))
    alerts, _st, _n = _both(wref, rules, cal, inp)
    assert ["W04_TIME", "exit_signal"] in _levels(alerts)
    assert ["W06_DATA_HEALTH", "warn"] in _levels(alerts)


def test_W06_f_stale_leg_keeps_w04_exit(ref, wref, rules, cal):
    """Reproduced D04 (b): one stale leg quote does not remove a W04 expiry exit that uses no quotes."""
    inp = _case(ref, rules, **_expiry(
        "2026-10-30", "14:00", "-04:00", "13:59",
        **{"legs.1.ask.as_of": "2026-10-30T13:30:00-04:00"},
    ))
    alerts, _st, _n = _both(wref, rules, cal, inp)
    assert ["W04_TIME", "exit_signal"] in _levels(alerts)
    assert ["W06_DATA_HEALTH", "warn"] in _levels(alerts)


def test_W06_g_stale_underlying_still_suppresses_w01(ref, wref, rules, cal):
    """W06-g / W06-d: stale underlying.last still suppresses a W01 exit. Field-granular suppression stays deferred."""
    inp = _case(ref, rules, **{
        **BREACH,
        "underlying.last.as_of": "2026-10-20T10:30:00-04:00",
    })
    alerts, _st, _n = _both(wref, rules, cal, inp)
    assert ["W01_MARK_VS_STOP_TARGET", "exit_signal"] not in _levels(alerts)
    assert ["W06_DATA_HEALTH", "warn"] in _levels(alerts)


def test_stale_leg_quote_still_suppresses_w01(ref, wref, rules, cal):
    """A stale leg quote still suppresses the W01 exit. That case is per spec and is not changed."""
    inp = _case(ref, rules, **{
        **BREACH,
        "legs.1.ask.as_of": "2026-10-20T10:30:00-04:00",
    })
    alerts, _st, _n = _both(wref, rules, cal, inp)
    assert ["W01_MARK_VS_STOP_TARGET", "exit_signal"] not in _levels(alerts)
    assert ["W06_DATA_HEALTH", "warn"] in _levels(alerts)


def _run(script: Path, *args):
    return subprocess.run([PY, "-B", str(script), *map(str, args)], capture_output=True, text=True, cwd=str(script.parent))


def _spec_mod(name):
    if str(SPEC) not in sys.path:
        sys.path.insert(0, str(SPEC))
    return __import__(name)


def test_CLI_empty_argv_session():
    """CLI-empty-argv-session: no path exits 2. The bundled example is not a silent default."""
    validate_session = _spec_mod("validate_session")
    assert validate_session.main([]) == 2
    r = _run(SPEC / "validate_session.py")
    assert r.returncode == 2
    assert "path" in r.stderr.lower()
    assert "OK:" not in r.stdout


def test_CLI_empty_argv_market():
    """CLI-empty-argv-market: no path exits 2. The example pack is not a silent default."""
    validate_market_snapshot = _spec_mod("validate_market_snapshot")
    assert validate_market_snapshot.main([]) == 2
    r = _run(SPEC / "validate_market_snapshot.py")
    assert r.returncode == 2
    assert "path" in r.stderr.lower()
    assert "OK:" not in r.stdout


def test_CLI_recursion_session_exit2(tmp_path):
    """CLI-recursion-session-exit2: deeply nested JSON is unparsable, exit 2, not 1."""
    path = tmp_path / "deep.json"
    path.write_text("[" * 100000 + "]" * 100000)
    r = _run(SPEC / "validate_session.py", path)
    assert r.returncode == 2, r.stderr[-500:]
    assert "RecursionError" in r.stderr


def test_CLI_loader_import_fail_exit2(tmp_path):
    """CLI-loader-import-fail-exit2: a missing market_data_loader on a supplied path exits 2."""
    dest = tmp_path / "spec"
    dest.mkdir()
    for name in ("validate_market_snapshot.py", "market_snapshot.schema.json", "market_data_rules.yaml"):
        shutil.copy(SPEC / name, dest / name)
    doc = dest / "doc.json"
    doc.write_text("{}")
    r = _run(dest / "validate_market_snapshot.py", doc)
    assert r.returncode == 2, r.stderr[-500:]


def test_CLI_broken_market_rules_yaml_exit2(tmp_path):
    """Broken market_data_rules.yaml is a validator crash: exit 2, not 1."""
    dest = tmp_path / "spec"
    dest.mkdir()
    for name in ("validate_market_snapshot.py", "market_snapshot.schema.json", "market_data_loader.py"):
        shutil.copy(SPEC / name, dest / name)
    (dest / "market_data_rules.yaml").write_text(":\n  - [")
    doc = dest / "doc.json"
    doc.write_text("{}")
    r = _run(dest / "validate_market_snapshot.py", doc)
    assert r.returncode == 2, r.stderr[-500:]
    assert r.stderr.strip()


def test_CLI_worst_code_wins(tmp_path):
    """A rule failure is exit 1. A crash on another path makes the worst code 2."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "9.9.9"}))
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000 + "]" * 100000)
    one = _run(SPEC / "validate_session.py", bad)
    assert one.returncode == 1
    both = _run(SPEC / "validate_session.py", bad, deep)
    assert both.returncode == 2
