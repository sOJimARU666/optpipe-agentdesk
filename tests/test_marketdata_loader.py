"""FEAT-OODLE-01 PR A: optpipe.marketdata, the app's fail-closed loader for Oodle's market-data packs.

Parity with Doodle's reference loader (spec 1.1.8 market_data_loader.py) on every committed fixture, the
spec-required fail-closed matrix (stale / missing / tampered / malformed / oversized / wrong-path / symlinked /
future-dated / unknown-field), the field helpers, the CLI, and the PAPER ONLY / no-network guarantees.
Offline: tmp_path copies of committed fixtures and an injected clock only."""
from __future__ import annotations

import ast
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from mdpack import (BUILT, DATE, FIX, FIX117, NOW, ROOT, SPEC, assert_closed, copy_pack, drop_file, edit_json,
                    edit_text, ids, load, rehash)
from optpipe import marketdata as md

sys.path.insert(0, str(SPEC))
import build_market_data_fixtures as bmf  # noqa: E402
import market_data_loader as mdl  # noqa: E402

CASES = [("1.1.8", c) for c in sorted(p.name for p in FIX.iterdir() if p.is_dir())] + \
        [("1.1.7", c) for c in sorted(p.name for p in FIX117.iterdir() if p.is_dir())]


# ------------------------------------------------------------------ parity with the spec (MD-01..MD-15)
@pytest.mark.parametrize("rel,case", CASES, ids=[f"{r}-{c}" for r, c in CASES])
def test_parity_with_reference_loader_on_every_committed_fixture(rel, case):
    """MD-01..MD-15 (spec 1.1.8): same error list, ok flag and exit code as the reference loader"""
    d = (FIX if rel == "1.1.8" else FIX117) / case / DATE
    ours, ref = load(d), mdl.load_pack(d)
    assert ours.errors == ref.errors
    assert (ours.ok, ours.exit_code()) == (ref.ok, ref.exit_code())
    if rel == "1.1.8":
        rc, want = bmf.EXPECTED[case]
        assert ours.exit_code() == rc and want <= ids(ours.errors)


def test_valid_pack_loads_with_everything_exposed():
    r = load(FIX / "valid" / DATE)
    assert r.ok and r.exit_code() == 0 and r.errors == []
    assert r.schema_version == "1.1.8" and r.index["pack_id"] == "EXAMPLE-2026-10-20" and r.date == DATE
    assert sorted(r.snapshots) == ["JPM", "WDC", "XMPLA", "XMPLB"]
    assert r.catalysts["kind"] == "catalyst_log" and r.summary_md.strip()


def test_valid_tmp_copy_loads(tmp_path):
    d = copy_pack(tmp_path)
    assert load(d).ok
    rehash(d)                          # the test helper reproduces the committed manifest byte for byte
    assert load(d).ok


# ------------------------------------------------------------------ fail-closed matrix (Sol R7-06 / MD-21-MATRIX)
def test_stale_quote_fails_closed():
    """MD-05 (20 min vs pack_built_at, inclusive): the spec 'stale' fixture"""
    assert_closed(load(FIX / "stale" / DATE), "MD-05-STALE")


def test_stale_boundary_is_inclusive(tmp_path):
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"as_of": "2026-10-20T08:50:00-04:00"', '"as_of": "2026-10-20T08:40:00-04:00"')
    assert load(d).ok, load(d).errors   # exactly 20 min: still OK
    d2 = copy_pack(tmp_path / "b")
    edit_text(d2, "JPM.json", '"as_of": "2026-10-20T08:50:00-04:00"', '"as_of": "2026-10-20T08:39:59-04:00"')
    assert_closed(load(d2), "MD-05-STALE")


def test_missing_date_dir_fails_closed(tmp_path):
    root = tmp_path.resolve() / "md"
    root.mkdir(mode=0o755)
    assert_closed(md.load_date(root, DATE, now=NOW, spec=SPEC), "MD-01-DIR", rc=2)


def test_missing_root_fails_closed(tmp_path):
    assert_closed(md.load_date(tmp_path.resolve() / "nope", DATE, now=NOW, spec=SPEC), "MD-01-DIR", rc=2)


def test_missing_manifest_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    (d / "pack.sha256").unlink()
    assert_closed(load(d), "MD-02-MISSING")


def test_missing_listed_file_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    (d / "WDC.json").unlink()
    assert_closed(load(d), "MD-02-ABSENT")


def test_missing_index_entry_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    drop_file(d, "WDC.json")
    assert_closed(load(d), "MD-02-INDEX")


def test_missing_value_is_unknown_not_ok():
    assert_closed(load(FIX / "missing_value" / DATE), "MD-06-UNKNOWN")


def test_tampered_file_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"value": 101.25', '"value": 111.25', hash_it=False)
    assert_closed(load(d), "MD-02-HASH")


def test_tampered_manifest_line_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    m = d / "pack.sha256"
    m.write_text(m.read_text().replace("  ", " ", 1))
    assert_closed(load(d), "MD-02-FORMAT")


def test_malformed_json_fails_closed_exit_2(tmp_path):
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"value": 101.25', '"value": 101.25,,')
    assert_closed(load(d), "MD-03-JSON", rc=2)


def test_unknown_field_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", lambda doc: doc.update(surprise=1))
    assert_closed(load(d), "MD-03-SCHEMA")


def test_unknown_field_in_a_quote_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", lambda doc: doc["last_price"].update(confidence="high"))
    assert_closed(load(d), "MD-03-SCHEMA")


def test_unknown_file_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    (d / "notes.md").write_text("hi\n")
    (d / "notes.md").chmod(0o644)
    assert_closed(load(d), "MD-01-NAME")


def test_wrong_schema_version_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    edit_text(d, "index.json", '"schema_version": "1.1.8"', '"schema_version": "9.9.9"')
    assert not load(d).ok


def test_oversized_file_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    p = d / "summary.md"
    p.write_bytes(p.read_bytes() + b"x" * (1024 * 1024 + 1))
    rehash(d)
    assert_closed(load(d), "MD-01-SIZE")


def test_wrong_path_manifest_traversal_fails_closed():
    assert_closed(load(FIX / "path_traversal" / DATE), "MD-02-PATH")


def test_symlinked_date_dir_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    alias = d.parent / "2026-10-21"
    alias.symlink_to(d.name)
    assert_closed(md.load_date(d.parent, "2026-10-21", now=NOW, spec=SPEC), "MD-01-SYMLINK")


def test_symlinked_file_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    outside = tmp_path / "evil.md"
    outside.write_bytes((d / "summary.md").read_bytes())
    (d / "summary.md").unlink()
    (d / "summary.md").symlink_to(outside)
    assert_closed(load(d), "MD-01-SYMLINK")


def test_future_dated_pack_fails_closed(tmp_path):
    """MD-19-FUTURE-PACK: a clock before pack_built_at; the whole pack fails"""
    assert_closed(load(FIX / "valid" / DATE, now=BUILT - timedelta(seconds=1)), "MD-19-FUTURE-PACK")
    assert load(FIX / "valid" / DATE, now=BUILT).ok          # inclusive at the build instant


def test_errors_never_leave_data_behind(tmp_path):
    d = copy_pack(tmp_path)
    edit_text(d, "WDC.json", '"ticker": "WDC"', '"ticker": "wdc"')
    r = load(d)
    assert_closed(r)


# ------------------------------------------------------------------ helpers
def test_field_usable_is_fail_closed():
    ok = {"value": 1.0, "status": "OK"}
    assert md.field_usable(ok) and md.usable_value(ok) == 1.0
    for f in ({"value": 1.0, "status": "STALE"}, {"value": None, "status": "OK"}, {"value": 1, "status": "UNKNOWN"},
              {"value": 1, "status": "UNCONFIRMED"}, {"value": float("nan"), "status": "OK"},
              {"value": float("inf"), "status": "OK"}, None, "OK", {}):
        assert not md.field_usable(f) and md.usable_value(f) is None


def test_constants_follow_the_pinned_spec():
    rules = yaml.safe_load((SPEC / "market_data_rules.yaml").read_text())
    assert load(FIX / "valid" / DATE).quote_limit == timedelta(minutes=rules["staleness"]["staleness_limit_minutes"])
    assert not hasattr(md, "QUOTE_LIMIT")   # read from the pinned spec at load time, not hard-coded
    assert md.QUOTE_LEAVES == set(rules["staleness"]["quote_fields"])
    assert "clock_skew_seconds" not in str(rules) and md.DEFAULT_SKEW_SECONDS == 0   # spec sets no skew: 0


def test_exit_codes():
    assert md.exit_code([]) == 0
    assert md.exit_code(["MD-05-STALE x"]) == 1
    for fatal in ("MD-01-DIR", "MD-01-UNREADABLE", "MD-03-JSON", "MD-00-CRASH", "MD-00-SPEC"):
        assert md.exit_code(["MD-05-STALE x", f"{fatal} y"]) == 2


def test_unpinned_spec_fails_closed_exit_2(tmp_path):
    assert_closed(load(FIX / "valid" / DATE, spec=tmp_path), "MD-00-SPEC", rc=2)


def test_any_exception_is_md00_crash(monkeypatch):
    monkeypatch.setattr(md, "_load_fd", lambda *a, **k: 1 / 0)
    assert_closed(load(FIX / "valid" / DATE), "MD-00-CRASH", rc=2)


# ------------------------------------------------------------------ paper only, no network, file I/O only
def test_loader_imports_no_network_module():
    tree = ast.parse((ROOT / "src" / "optpipe" / "marketdata.py").read_text())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
    assert mods <= {"__future__", "collections", "errno", "hashlib", "json", "math", "os", "re", "stat",
                    "dataclasses", "datetime", "pathlib", "types", "typing", "zoneinfo"}, mods
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            called.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "")
    writers = {"open", "write", "write_text", "write_bytes", "unlink", "remove", "rename", "replace", "mkdir",
               "rmdir", "chmod", "symlink", "link", "urlopen", "connect", "socket", "system", "popen", "exec"}
    assert not (called - {"open"}) & writers, called & writers
    # exactly one open call in the module: os.open inside _open_ro, which refuses every write flag
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", ""))
             == "open"]
    assert len(opens) == 1 and ast.unparse(opens[0]) == "os.open(name, flags, dir_fd=dir_fd)"
    for flag in (os.O_WRONLY, os.O_RDWR, os.O_CREAT | os.O_RDONLY, os.O_TRUNC, os.O_APPEND):
        with pytest.raises(ValueError):
            md._open_ro("x", flag)


def test_loader_never_writes(tmp_path):
    d = copy_pack(tmp_path)
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in d.iterdir()}
    load(d)
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in d.iterdir()} == before


# ------------------------------------------------------------------ CLI
def _cli(monkeypatch, capsys, *argv, env_dir=None):
    from optpipe.cli import main
    monkeypatch.setenv("OPTIONS_SPEC_DIR", str(SPEC))
    if env_dir is not None:
        monkeypatch.setenv(md.ENV_DATA_DIR, str(env_dir))
    rc = main(["market-data", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_valid_pack(monkeypatch, capsys):
    rc, out, err = _cli(monkeypatch, capsys, "--data-dir", str(FIX / "valid"), "--date", DATE,
                        "--now", NOW.isoformat())
    assert rc == 0, err
    assert out.startswith("OK: spec/fixtures/market_data/valid/2026-10-20 valid (market_snapshot pack, "
                          "schema_version 1.1.8, data_label EXAMPLE, 4 ticker(s)")
    assert "JPM      NO IDEA: MD-20-SCOPE" in out           # EXAMPLE data is never a real idea
    assert str(ROOT) not in out + err and str(Path.home()) not in out + err     # repo-relative display only


def test_cli_env_var_and_exit_codes(monkeypatch, capsys, tmp_path):
    d = copy_pack(tmp_path, "stale")
    rc, out, err = _cli(monkeypatch, capsys, "--date", DATE, "--now", NOW.isoformat(), env_dir=d.parent)
    assert rc == 1 and "MD-05-STALE" in err and "FAIL:" in err and out == ""
    rc, _, err = _cli(monkeypatch, capsys, "--date", "2026-10-21", "--now", NOW.isoformat(), env_dir=d.parent)
    assert rc == 2 and "MD-01-DIR" in err


def test_cli_default_date_is_new_york_today(monkeypatch, capsys, tmp_path):
    d = copy_pack(tmp_path)
    rc, out, err = _cli(monkeypatch, capsys, "--now", "2026-10-20T13:05:00+00:00", env_dir=d.parent)
    assert rc == 0, err


def test_cli_needs_an_aware_clock(monkeypatch, capsys):
    rc, _, err = _cli(monkeypatch, capsys, "--data-dir", str(FIX / "valid"), "--date", DATE,
                      "--now", "2026-10-20T09:05:00")
    assert rc == 2 and "MD-19-TZ" in err


def test_data_root_comes_only_from_the_flag_or_env(monkeypatch):
    monkeypatch.delenv(md.ENV_DATA_DIR, raising=False)
    assert md.data_root_from_env() is None and not hasattr(md, "DEFAULT_DATA_DIR")
    monkeypatch.setenv(md.ENV_DATA_DIR, "")
    assert md.data_root_from_env() is None
    monkeypatch.setenv(md.ENV_DATA_DIR, "somewhere")
    assert md.data_root_from_env() == Path("somewhere")
