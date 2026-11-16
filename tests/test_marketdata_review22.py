"""PR #22 review round 1 (Moodle): no default root, root always confined, earnings_time and spread_pct_of_mid
in NO IDEA, the CONFIRMED-pair window, --date validation, (proposed) labels and the spec-read quote limit.
Offline: committed fixtures (OPTIONS_SPEC_DIR=spec) and tmp copies only."""
from __future__ import annotations

import builtins
import os
from datetime import timedelta
from pathlib import Path

import pytest

from mdpack import DATE, FIX, NOW, ROOT, SPEC, copy_pack, edit_json, ids, load, rehash
from optpipe import marketdata as md
from test_marketdata_r7 import _fresh_contract, _jpm_event_time


# ------------------------------------------------------------------ required 1: no live folder by default
def _record_fs(monkeypatch):
    """record every path-taking os / builtins call made while the CLI runs"""
    seen = []
    for mod, names in ((os, ("open", "stat", "lstat", "scandir", "listdir")), (builtins, ("open",))):
        for n in names:
            real = getattr(mod, n)

            def spy(*a, _real=real, _n=n, **kw):
                if a:
                    seen.append((_n, str(a[0])))
                return _real(*a, **kw)
            monkeypatch.setattr(mod, n, spy)
    return seen


def test_cli_without_a_root_exits_2_and_opens_nothing(monkeypatch, capsys):
    from optpipe.cli import main
    monkeypatch.setenv("OPTIONS_SPEC_DIR", str(SPEC))
    monkeypatch.delenv(md.ENV_DATA_DIR, raising=False)
    monkeypatch.chdir(ROOT)
    import optpipe.cli as cli
    settings = cli.load_settings(None)                     # settings are read before the command; not pack I/O
    monkeypatch.setattr(cli, "load_settings", lambda _p: settings)
    seen = _record_fs(monkeypatch)
    rc = main(["market-data", "--date", DATE, "--now", NOW.isoformat()])
    err = capsys.readouterr().err
    monkeypatch.undo()
    assert rc == 2 and err.startswith("MD-00-ROOT (app) no market-data root given"), err
    # argparse may stat gettext catalogs under /usr; nothing in or next to the repo (../trading, spec/, fixtures)
    near = [(f, p) for f, p in seen if not os.path.isabs(p) or os.path.abspath(p).startswith(str(ROOT.parent) + os.sep)]
    assert near == [], near
    assert not any("trading" in p or "market" in p for _, p in seen), seen


def test_empty_env_root_is_no_root(monkeypatch, capsys):
    from optpipe.cli import main
    monkeypatch.setenv("OPTIONS_SPEC_DIR", str(SPEC))
    monkeypatch.setenv(md.ENV_DATA_DIR, "")
    assert main(["market-data", "--date", DATE, "--now", NOW.isoformat()]) == 2
    assert "MD-00-ROOT" in capsys.readouterr().err


@pytest.mark.parametrize("call", [
    lambda: md.load_pack(FIX / "valid" / DATE, root=None, now=NOW, spec=SPEC),
    lambda: md.load_date(None, DATE, now=NOW, spec=SPEC),
    lambda: md.load_date("", DATE, now=NOW, spec=SPEC),
])
def test_api_without_a_root_is_md00_root_and_opens_nothing(call, monkeypatch):
    seen = _record_fs(monkeypatch)
    r = call()
    monkeypatch.undo()
    assert seen == [] and not r.ok and ids(r.errors) == {"MD-00-ROOT"} and r.exit_code() == 2, r.errors


def test_load_pack_requires_root_keyword():
    with pytest.raises(TypeError):
        md.load_pack(FIX / "valid" / DATE, now=NOW, spec=SPEC)       # no silent dirname(date_dir) root any more


def test_root_is_always_confined(tmp_path):
    d = copy_pack(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    r = md.load_pack(d, root=other, now=NOW, spec=SPEC)
    assert not r.ok and "MD-17-ROOT" in ids(r.errors), r.errors


# ------------------------------------------------------------------ required 3: earnings_time
def test_moodle_probe_earnings_time_amc_is_no_idea(tmp_path):
    """the probe: JPM earnings_time BMO -> AMC, nothing else. catalysts.json has no structured time for JPM (only a
    free-text note), so the pack itself cannot show a contradiction; the ticker is NO IDEA because its earnings_time
    cannot be checked -- even with fresh contracts and EXAMPLE allowed (not stale or scope by accident)."""
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", lambda x: (x["earnings_time"].update(value="AMC"), _fresh_contract(x)))
    r = load(d)
    assert r.ok, r.errors
    why = md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",))
    assert ids(why) == {"MD-20-UNVERIFIED"} and "JPM.earnings_time: AMC is not corroborated" in why[0], why


def test_earnings_time_contradicting_catalyst_event_time_fails_the_pack(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "catalysts.json", _jpm_event_time)                  # 07:00 NY = BMO
    edit_json(d, "JPM.json", lambda x: x["earnings_time"].update(value="AMC"))
    r = load(d)
    assert not r.ok and r.exit_code() == 1 and ids(r.errors) == {"MD-20-CONFLICT"}, r.errors
    assert "JPM.json earnings_time: AMC (OK) contradicts the CONFIRMED catalysts.json earnings event_time" in r.errors[0]


@pytest.mark.parametrize("t,value,ok", [
    ("2026-10-21T07:00:00-04:00", "BMO", True), ("2026-10-21T09:29:00-04:00", "BMO", True),
    ("2026-10-21T16:05:00-04:00", "AMC", True), ("2026-10-21T16:00:00-04:00", "AMC", True),
    ("2026-10-21T16:05:00-04:00", "BMO", False), ("2026-10-21T12:00:00-04:00", "BMO", False),
    ("2026-10-21T12:00:00-04:00", "AMC", False), ("2026-10-21T11:00:00+00:00", "BMO", True),   # 07:00 NY
])
def test_earnings_time_session_twins(tmp_path, t, value, ok):
    d = copy_pack(tmp_path)
    edit_json(d, "catalysts.json", lambda c: _jpm_event_time(c, t))
    edit_json(d, "JPM.json", lambda x: (x["earnings_time"].update(value=value), _fresh_contract(x)))
    r = load(d)
    assert r.ok is ok, r.errors
    if ok:
        assert md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",)) == []


def test_unknown_earnings_time_is_no_idea_under_r07():
    """Moodle to-do (b): UNKNOWN / null earnings_time is MD-20-CRITICAL (R07 cannot place the event). Fail closed."""
    r = load(FIX / "valid" / DATE)                                  # WDC 'unknown', XMPLA null
    assert r.ok
    for t in ("WDC", "XMPLA"):
        why = md.no_idea_reasons(r, t, now=NOW, allow_labels=("EXAMPLE",))
        assert "MD-20-CRITICAL" in ids(why) and any("earnings_time" in e for e in why), why


# ------------------------------------------------------------------ required 3: critical lists
def test_critical_lists_are_pinned_exactly():
    assert md.IDEA_TOP_FIELDS == ("last_price", "earnings_date")
    assert md.CONTRACT_CRITICAL == ("bid", "ask", "open_interest", "spread_pct_of_mid")
    assert set(md.CONTRACT_CRITICAL) - {"open_interest"} <= md.QUOTE_LEAVES     # the quote fields age at decision time


@pytest.mark.parametrize("how", ["stale", "unknown", "old"])
def test_spread_pct_of_mid_is_critical(tmp_path, how):
    d = copy_pack(tmp_path)

    def edit(x):
        _fresh_contract(x)
        f = x["option_liquidity"]["contracts"][0]["spread_pct_of_mid"]
        if how == "stale":
            f.update(status="STALE")
        elif how == "unknown":
            f.update(status="UNKNOWN", value=None)
        else:
            f.update(as_of="2026-10-20T08:40:00-04:00")              # 25 min old at 09:05 (> 20); pack still valid
    edit_json(d, "JPM.json", edit)
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    assert r.ok, r.errors                                            # a valid pack: the reason is decision-time only
    why = md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",))
    assert ids(why) == {"MD-20-CRITICAL"} and "spread_pct_of_mid" in why[0], why


# ------------------------------------------------------------------ nit: CONFIRMED pairs
def _add_jpm(date, status="CONFIRMED"):
    def fn(doc):
        c = dict(next(c for c in doc["catalysts"] if c["ticker"] == "JPM"))
        c.update(event_date=date, status=status)
        doc["catalysts"].append(c)
    return fn


def test_two_confirmed_quarters_87_days_apart_pass(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "catalysts.json", _add_jpm("2027-01-15"))           # Moodle's dup90 probe
    r = load(d)
    assert r.ok, r.errors


def test_two_confirmed_within_30_days_fail_with_a_true_message(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "catalysts.json", _add_jpm("2026-11-05"))
    r = load(d)
    assert not r.ok and ids(r.errors) >= {"MD-20-CONFLICT"}
    assert any("within 30 days [['2026-10-21', '2026-11-05']]" in e for e in r.errors), r.errors


def test_snapshot_must_name_the_next_confirmed_date(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "catalysts.json", _add_jpm("2027-01-15"))
    edit_json(d, "JPM.json", lambda x: x["earnings_date"].update(value="2027-01-15"))   # skips 10-21
    r = load(d)
    assert not r.ok and any("contradicts the next CONFIRMED catalysts.json earnings 2026-10-21" in e
                            for e in r.errors), r.errors


# ------------------------------------------------------------------ nit: quote limit from the spec
def test_quote_limit_is_read_from_the_spec(tmp_path, monkeypatch):
    r = load(FIX / "valid" / DATE)
    assert r.quote_limit == timedelta(minutes=20)
    vms = md._vms(SPEC)
    for bad in (None, 0, -5, "20", True):
        monkeypatch.setitem(vms.RULES["staleness"], "staleness_limit_minutes", bad)
        r = load(FIX / "valid" / DATE)
        assert not r.ok and ids(r.errors) == {"MD-00-SPEC"} and r.exit_code() == 2, (bad, r.errors)
    monkeypatch.setitem(vms.RULES["staleness"], "staleness_limit_minutes", 30)   # follows the spec, not a constant
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", lambda x: _fresh_contract(x, "08:40"))   # 25 min old at 09:05: NO IDEA under 20
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    assert r.ok and r.quote_limit == timedelta(minutes=30), r.errors
    assert md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",)) == []          # 25 min old < 30


# ------------------------------------------------------------------ nit: --date and labels
def _cli(monkeypatch, capsys, *argv):
    from optpipe.cli import main
    monkeypatch.setenv("OPTIONS_SPEC_DIR", str(SPEC))
    rc = main(["market-data", "--data-dir", str(FIX / "valid"), "--now", NOW.isoformat(), *argv])
    o = capsys.readouterr()
    return rc, o.out, o.err


@pytest.mark.parametrize("bad", ["2026-10-2", "2026-10-20\n", " 2026-10-20", "2026-13-01", "2026-02-30",
                                 "20261020", "2026-10-20/../x", "..", "２０２６-10-20"])
def test_cli_rejects_a_bad_date(monkeypatch, capsys, bad):
    rc, out, err = _cli(monkeypatch, capsys, "--date", bad)
    assert rc == 2 and "--date" in err and out == "", err


def test_cli_accepts_a_good_date(monkeypatch, capsys):
    rc, out, err = _cli(monkeypatch, capsys, "--date", DATE)
    assert rc == 0, err
    assert "NO IDEA: MD-20-SCOPE (proposed)" in out


def test_proposed_and_app_ids_are_labelled():
    errs = ["MD-16-DUPKEY x.json: dup", "MD-21-REPLAY x", "MD-17-ROOT y", "MD-00-SPEC z", "MD-00-ROOT", "MD-05-STALE a",
            "MD-00-CRASH b", "MD-15-TIME c", "MD-16-DUPKEY (proposed) already"]
    assert md.label_ids(list(errs)) == [
        "MD-16-DUPKEY (proposed) x.json: dup", "MD-21-REPLAY (proposed) x", "MD-17-ROOT (proposed) y",
        "MD-00-SPEC (app) z", "MD-00-ROOT (app)", "MD-05-STALE a", "MD-00-CRASH b", "MD-15-TIME c",
        "MD-16-DUPKEY (proposed) already"]
    assert [md.error_id(e) for e in md.label_ids(list(errs))][:4] == [
        "MD-16-DUPKEY (proposed)", "MD-21-REPLAY (proposed)", "MD-17-ROOT (proposed)", "MD-00-SPEC (app)"]


def test_runtime_errors_carry_the_label(tmp_path):
    d = copy_pack(tmp_path)
    (d / "JPM.json").write_text('{"a": 1, "a": 2}')
    rehash(d)
    r = load(d)
    assert any(e.startswith("MD-16-DUPKEY (proposed) ") for e in r.errors), r.errors
    assert all(not e.startswith("MD-0") or "(proposed)" not in e for e in r.errors)


def test_future_errors_say_how_many_more(tmp_path):
    from mdpack import BUILT
    r = load(FIX / "valid" / DATE, now=BUILT - timedelta(seconds=1))
    assert ids(r.errors) == {"MD-19-FUTURE-PACK"}
    vms = md._vms(SPEC)
    docs = {f"T{i}.json": {"x": {"as_of": "2026-10-21T00:00:00-04:00"}} for i in range(25)}
    out = md._future_errors(vms, {}, docs, None, NOW, timedelta(0))
    assert len(out) == 21 and out[-1] == "MD-19-FUTURE pack: ... and 5 more as_of after the clock (first 20 listed)"
