"""FEAT-OODLE-01 PR A: Sol's R7 criteria and the reviewer holes (a)..(h), tagged with the ids Doodle PROPOSED in
trading/proposals/R7-acceptance-proposal.md (MD-16 = R7-01 ... MD-21 = R7-06). The ids are a proposal, not spec 1.1.8;
spec/ is unchanged. Each fail case has a pass twin from the valid fixture. Offline: tmp_path copies of committed
fixtures and an injected clock only. Holes marked HOLE load ok=True through the spec 1.1.8 reference loader
(see the PR body for the before/after evidence)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from mdpack import (BUILT, DATE, FIX, NOW, ROOT, SPEC, assert_closed, copy_pack, drop_file, edit_json, edit_text,
                    ids, load, rehash)
from optpipe import marketdata as md


# ================================================================== MD-16 (R7-01): untrusted files, strict parsing
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_md16_nonfinite_numbers_fail_closed(tmp_path, literal):
    """HOLE (b): NaN / Infinity / 1e400 load as OK in the reference loader"""
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"value": 101.25', f'"value": {literal}')
    assert_closed(load(d), "MD-16-NONFINITE")


def test_md16_huge_integer_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "WDC.json", lambda doc: doc["option_liquidity"]["summary"]["volume"].update(value=2 ** 60))
    assert_closed(load(d), "MD-16-NONFINITE")


def test_md16_finite_twin_loads(tmp_path):
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"value": 101.25', '"value": 1.0125e2')
    assert load(d).ok


@pytest.mark.parametrize("where", ["top", "nested"])
def test_md16_duplicate_keys_fail_closed(tmp_path, where):
    """HOLE (c): duplicate keys are silently last-wins in the reference loader"""
    d = copy_pack(tmp_path)
    if where == "top":
        edit_text(d, "JPM.json", '"ticker": "JPM",', '"ticker": "JPM", "ticker": "JPM",')
    else:
        edit_text(d, "JPM.json", '"value": 101.25', '"value": 999.0, "value": 101.25')
    assert_closed(load(d), "MD-16-DUPKEY")


def test_md16_pack_id_trailing_newline_fails_closed(tmp_path):
    """HOLE (e): `$` in the schema pattern lets 'EXAMPLE-2026-10-20\\n' through; identifiers must match whole"""
    d = copy_pack(tmp_path)
    for name in ("index.json", "JPM.json", "WDC.json", "XMPLA.json", "XMPLB.json", "catalysts.json"):
        edit_text(d, name, '"pack_id": "EXAMPLE-2026-10-20"', '"pack_id": "EXAMPLE-2026-10-20\\n"', hash_it=False)
    rehash(d)
    assert_closed(load(d), "MD-16-PATTERN")


@pytest.mark.parametrize("ch", ["\\n", "\\u0000", "\\u001f", "\\u007f", "\\u2028"])
def test_md16_control_characters_in_any_string_fail_closed(tmp_path, ch):
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"source": "example_ir"', f'"source": "example_ir{ch}"')
    assert_closed(load(d), "MD-16-PATTERN")


def test_md16_control_character_in_a_key_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"ticker": "JPM",', '"ticker\\t": "JPM",')
    assert not load(d).ok and "MD-16-PATTERN" in ids(load(d).errors)


def test_md16_empty_tickers_fail_closed(tmp_path):
    """HOLE (f): an index with zero tickers is OK in the reference loader"""
    d = copy_pack(tmp_path)
    for t in ("JPM", "WDC", "XMPLA", "XMPLB"):
        drop_file(d, f"{t}.json")
    drop_file(d, "catalysts.json")
    edit_json(d, "index.json", lambda doc: doc.update(tickers=[]))
    r = load(d)
    assert_closed(r, "MD-16-EMPTY")


def test_md16_too_many_entries_fails_closed_before_any_stat(tmp_path, monkeypatch):
    d = copy_pack(tmp_path)
    monkeypatch.setattr(md, "MAX_ENTRIES", 5)        # 8 entries in the example pack
    stats = []
    real = os.stat
    monkeypatch.setattr(md.os, "stat", lambda *a, **k: stats.append(k) or real(*a, **k))
    assert_closed(load(d), "MD-16-ENTRIES")
    assert not [k for k in stats if "dir_fd" in k]   # counted with a bounded iterator before any entry stat


def test_md16_entries_bound_twin(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "MAX_ENTRIES", 8)
    assert load(copy_pack(tmp_path)).ok


def test_md16_total_bytes_fails_closed(tmp_path, monkeypatch):
    d = copy_pack(tmp_path)
    monkeypatch.setattr(md, "MAX_PACK_BYTES", 4096)
    assert_closed(load(d), "MD-16-TOTAL")


@pytest.mark.parametrize("bad", [b"\xef\xbb\xbf", b"\xff"])
def test_md16_encoding_is_a_coded_error_not_a_crash(tmp_path, bad):
    d = copy_pack(tmp_path)
    p = d / "JPM.json"
    p.write_bytes(bad + p.read_bytes() if bad.startswith(b"\xef") else p.read_bytes().replace(b"JPM", b"J\xffM", 1))
    rehash(d)
    assert_closed(load(d), "MD-16-ENCODING", "MD-03-JSON", rc=2)


def test_md16_summary_invalid_utf8(tmp_path):
    d = copy_pack(tmp_path)
    p = d / "summary.md"
    p.write_bytes(p.read_bytes() + b"\xff\n")
    rehash(d)
    assert_closed(load(d), "MD-16-ENCODING", rc=2)


# ================================================================== MD-17 (R7-02): paths, links, races, confinement
def test_md17_hard_link_fails_closed(tmp_path):
    """HOLE (g): a hard-linked summary.md (bytes shared with a file outside the pack) is OK in the reference loader"""
    d = copy_pack(tmp_path)
    os.link(d / "summary.md", tmp_path / "outside.md")
    assert_closed(load(d), "MD-17-HARDLINK")


def test_md17_symlink_out_of_the_pack_fails_closed(tmp_path):
    """(g): a ticker file that is a symlink to a byte-identical file outside the pack dir"""
    d = copy_pack(tmp_path)
    outside = tmp_path / "JPM.json"
    outside.write_bytes((d / "JPM.json").read_bytes())
    (d / "JPM.json").unlink()
    (d / "JPM.json").symlink_to(outside)
    assert_closed(load(d), "MD-01-SYMLINK")


def test_md17_symlink_inside_the_pack_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    (d / "summary.md").rename(d / "real.md")
    (d / "summary.md").symlink_to("real.md")
    assert_closed(load(d), "MD-01-SYMLINK")


def test_md17_symlinked_ancestor_fails_closed(tmp_path):
    """the root walk opens each component with O_NOFOLLOW: no symlinked ancestor"""
    d = copy_pack(tmp_path)
    link = tmp_path.resolve() / "alias"
    link.symlink_to(d.parent.parent)
    assert_closed(md.load_date(link / "valid", DATE, now=NOW, spec=SPEC), "MD-01-SYMLINK")


def test_md17_date_dir_must_be_a_direct_child_of_the_root(tmp_path):
    d = copy_pack(tmp_path)
    assert_closed(load(d, root=d.parent.parent), "MD-17-ROOT")
    assert load(d, root=d.parent).ok


@pytest.mark.parametrize("name", ["2026-02-30", "2026-10-20x", "2026-10-20\n", "..", "20261020"])
def test_md17_date_dir_name_must_be_a_real_date(tmp_path, name):
    d = copy_pack(tmp_path)
    r = md.load_date(d.parent, name, now=NOW, spec=SPEC)
    assert not r.ok and ids(r.errors) & {"MD-17-ROOT"}, r.errors


@pytest.mark.parametrize("target", ["root", "date", "file"])
def test_md17_writable_by_others_fails_closed(tmp_path, target):
    d = copy_pack(tmp_path)
    {"root": d.parent, "date": d, "file": d / "JPM.json"}[target].chmod(0o777 if target != "file" else 0o666)
    assert_closed(load(d), "MD-17-WRITABLE")


def test_md17_fifo_and_exec_bit_fail_closed(tmp_path):
    d = copy_pack(tmp_path)
    os.mkfifo(d / "ZZZ.json")
    assert_closed(load(d), "MD-01-NOT-REGULAR")
    d2 = copy_pack(tmp_path / "b")
    (d2 / "JPM.json").chmod(0o755)
    assert_closed(load(d2), "MD-01-EXEC")


def test_md17_subdirectory_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    (d / "AAA.json").mkdir(mode=0o755)
    assert_closed(load(d), "MD-01-NAME")


def test_md17_vanished_entry_is_coded_not_a_crash(tmp_path, monkeypatch):
    d = copy_pack(tmp_path)
    real = md._list
    monkeypatch.setattr(md, "_list", lambda fd, e: sorted(real(fd, e) + ["GONE.json"]))
    assert_closed(load(d), "MD-17-VANISH")


def test_md17_file_replaced_between_scan_and_open(tmp_path, monkeypatch):
    d = copy_pack(tmp_path)
    real = md._read

    def racing(dfd, name, *a, **k):
        if name == "WDC.json":
            data = (d / name).read_bytes()
            (d / name).unlink()
            (d / name).write_bytes(data)       # same bytes, new inode (an attacker's swap)
            (d / name).chmod(0o644)
            os.utime(d / name, ns=(2, 2))     # even if the inode number is reused, the stat signature differs
        return real(dfd, name, *a, **k)
    monkeypatch.setattr(md, "_read", racing)
    assert_closed(load(d), "MD-17-SNAPSHOT")


def test_md17_file_changed_after_it_was_read(tmp_path, monkeypatch):
    d = copy_pack(tmp_path)
    real = md._read

    def racing(dfd, name, *a, **k):
        out = real(dfd, name, *a, **k)
        if name == "XMPLB.json":
            os.utime(d / "JPM.json", ns=(1, 1))
        return out
    monkeypatch.setattr(md, "_read", racing)
    assert_closed(load(d), "MD-17-SNAPSHOT")


def test_md17_foreign_owner_fails_closed(tmp_path, monkeypatch):
    uid = os.getuid()
    if uid == 0:
        pytest.skip("running as root: root-owned files are accepted")
    d = copy_pack(tmp_path)
    monkeypatch.setattr(md.os, "getuid", lambda: uid + 1)
    assert_closed(load(d), "MD-17-OWNER")


# ================================================================== MD-18 (R7-03): integrity vs provenance
def test_md18_honest_rehash_still_loads(tmp_path):
    """MD-18 is DEFERRED (builder / ingest provenance binding). pack.sha256 is integrity over the exact bytes parsed,
    not provenance: a writer who also rewrites the manifest still gets a valid pack. Documented residual."""
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"value": 101.25', '"value": 99.5')     # a writer who also rewrites pack.sha256
    assert load(d).ok                                                  # (residual risk: no provenance binding yet)


# ================================================================== MD-19 (R7-04): clock, future, tz, skew
def _shift_year(d, year: str):
    for p in d.iterdir():
        if p.name != "pack.sha256":
            p.write_text(p.read_text().replace("2026-10-", f"{year}-10-"))
    rehash(d)
    new = d.parent / d.name.replace("2026", year)
    d.rename(new)
    return new


def test_md19_pack_from_2031_with_a_matching_date_dir_fails_closed(tmp_path):
    """HOLE (d): every time and the dir say 2031; the reference loader (no clock) loads it ok=True"""
    d = _shift_year(copy_pack(tmp_path), "2031")
    assert_closed(load(d, now=NOW), "MD-19-FUTURE-PACK", "MD-19-FUTURE")
    later = NOW.replace(year=2031)
    assert load(d, now=later).ok, load(d, now=later).errors          # twin: the same pack at its own time


def test_md19_skew_is_zero_by_default_and_injectable(tmp_path):
    early = BUILT - timedelta(seconds=30)
    assert_closed(load(FIX / "valid" / DATE, now=early), "MD-19-FUTURE-PACK")
    assert load(FIX / "valid" / DATE, now=early, skew_seconds=60).ok
    assert_closed(load(FIX / "valid" / DATE, now=BUILT - timedelta(seconds=61), skew_seconds=60), "MD-19-FUTURE-PACK")


def test_md19_field_after_the_build_fails_closed(tmp_path):
    """a field as_of after pack_built_at is the spec's MD-04-FUTURE (the clock never makes it acceptable)"""
    d = copy_pack(tmp_path)
    edit_text(d, "catalysts.json", '"as_of": "2026-10-20T08:30:00-04:00"', '"as_of": "2026-10-20T09:04:00-04:00"')
    assert_closed(load(d, now=NOW + timedelta(hours=1)), "MD-04-FUTURE")


def test_md19_future_backstop_covers_every_as_of_at_any_depth():
    """MD-19-FUTURE: any as_of / sources[].as_of / nested source.as_of later than the clock"""
    from optpipe.marketdata import _future_errors
    idx = {"pack_built_at": "2026-10-20T09:00:00-04:00"}
    cat = {"x": [{"deep": {"source": {"as_of": "2026-10-20T09:06:00-04:00"}}}]}
    got = _future_errors(None, idx, {"JPM.json": {"last_price": {"as_of": "2026-10-20T09:05:00-04:00"}}}, cat,
                         NOW, timedelta(0))
    assert got == ["MD-19-FUTURE catalysts.json x[0].deep.source.as_of: 2026-10-20T09:06:00-04:00 is after the "
                   "clock 2026-10-20T09:05:00-04:00"]


def test_md19_naive_clock_fails_closed():
    assert_closed(load(FIX / "valid" / DATE, now=datetime(2026, 10, 20, 9, 5)), "MD-19-TZ")


def test_md19_naive_pack_time_fails_closed(tmp_path):
    d = copy_pack(tmp_path)
    edit_text(d, "JPM.json", '"as_of": "2026-10-20T08:50:00-04:00"', '"as_of": "2026-10-20T08:50:00"')
    assert_closed(load(d), "MD-04-TIME-NAIVE")


def test_md19_clock_in_any_zone_is_the_same_instant():
    for tz in (timezone.utc, timezone(timedelta(hours=1)), timezone(timedelta(hours=-4))):
        assert load(FIX / "valid" / DATE, now=NOW.astimezone(tz)).ok


# ================================================================== MD-20 (R7-05): consumed fields, NO IDEA
def test_md20_earnings_date_contradicting_a_confirmed_catalyst_fails_closed(tmp_path):
    """HOLE (h): JPM earnings_date 2026-10-22 vs a CONFIRMED catalysts.json earnings on 2026-10-21"""
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", lambda doc: doc["earnings_date"].update(value="2026-10-22"))
    assert_closed(load(d), "MD-20-CONFLICT")


def test_md20_two_earnings_dates_within_30_days_fail_closed(tmp_path):
    d = copy_pack(tmp_path)

    def dup(doc):
        c = dict(doc["catalysts"][0])
        c["event_date"] = "2026-10-29"
        doc["catalysts"].append(c)
    edit_json(d, "catalysts.json", dup)
    r = load(d)
    assert not r.ok and "MD-20-CONFLICT" in ids(r.errors), r.errors


def _fresh_contract(doc, minute="08:55"):
    for k in md.CONTRACT_CRITICAL:
        f = doc["option_liquidity"]["contracts"][0][k]
        f.update(status="OK", as_of=f"2026-10-20T{minute}:00-04:00")


def test_md20_no_idea_pass_twin(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", _fresh_contract)
    edit_json(d, "catalysts.json", _jpm_event_time)
    r = load(d)
    assert r.ok, r.errors
    assert md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",)) == []


def _jpm_event_time(doc, t="2026-10-21T07:00:00-04:00"):
    """give the JPM CONFIRMED earnings entry a structured event_time (07:00 NY = BMO, as the snapshot says)"""
    next(c for c in doc["catalysts"] if c["ticker"] == "JPM" and c["catalyst_type"] == "earnings")["event_time"] = t


def test_md20_example_data_is_never_a_real_idea():
    r = load(FIX / "valid" / DATE)
    assert ids(md.no_idea_reasons(r, "JPM", now=NOW)) == {"MD-20-SCOPE"}


@pytest.mark.parametrize("ticker,want", [
    ("JPM", {"MD-20-CRITICAL", "MD-20-CONFLICT"}),   # every contract STALE; BMO has no catalyst event_time
    ("WDC", {"MD-20-CRITICAL"}),          # earnings_date UNCONFIRMED, no contracts
    ("XMPLA", {"MD-20-CRITICAL"}),        # earnings_date UNKNOWN
    ("XMPLB", {"MD-20-CRITICAL"}),
    ("NOPE", {"MD-20-NO-IDEA"}),
])
def test_md20_critical_unknown_or_stale_means_no_idea(ticker, want):
    r = load(FIX / "valid" / DATE)
    got = md.no_idea_reasons(r, ticker, now=NOW, allow_labels=("EXAMPLE",))
    assert got and ids(got) == want, got


def test_md20_decision_time_stale_quote_means_no_idea(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "JPM.json", _fresh_contract)
    r = load(d)
    later = BUILT + timedelta(minutes=16)                  # last_price as_of 08:50 -> 26 min old
    assert "MD-19-STALE-quote" in ids(md.no_idea_reasons(r, "JPM", now=later, allow_labels=("EXAMPLE",)))


def test_md20_unconfirmed_conflict_means_no_idea(tmp_path):
    d = copy_pack(tmp_path)
    edit_json(d, "WDC.json", lambda doc: doc["earnings_date"].update(value="2026-10-29"))
    r = load(d)
    assert r.ok, r.errors                                  # UNCONFIRMED vs UNCONFIRMED: not pack-fatal ...
    assert "MD-20-CONFLICT" in ids(md.no_idea_reasons(r, "WDC", now=NOW, allow_labels=("EXAMPLE",)))  # ... but NO IDEA


def test_md20_failed_pack_means_no_idea_for_everyone():
    r = load(FIX / "stale" / DATE)
    for t in ("JPM", "WDC"):
        assert ids(md.no_idea_reasons(r, t, now=NOW, allow_labels=("EXAMPLE",))) == {"MD-20-NO-IDEA"}


def test_md20_naive_decision_clock_means_no_idea():
    r = load(FIX / "valid" / DATE)
    assert ids(md.no_idea_reasons(r, "JPM", now=datetime(2026, 10, 20, 9, 5))) == {"MD-19-TZ"}


# ================================================================== MD-21 (R7-06): deterministic offline matrix
MATRIX = {   # class -> (mutation, exact ids, exit code)
    "stale": ("stale", {"MD-05-STALE"}, 1),
    "missing": ("missing_manifest", {"MD-01-NAME", "MD-02-MISSING"}, 1),
    "tampered": ("tampered_hash", {"MD-02-HASH"}, 1),
    "malformed": ("malformed_json", {"MD-03-JSON"}, 2),
    "wrong-path": ("path_traversal", {"MD-02-PATH"}, 1),
}


@pytest.mark.parametrize("cls", sorted(MATRIX))
def test_md21_matrix_exact_ids(cls):
    case, want, rc = MATRIX[cls]
    r = load(FIX / case / DATE)
    assert_closed(r, *want, rc=rc)
    assert ids(r.errors) == want, r.errors


def test_md21_matrix_oversized_and_future_exact(tmp_path):
    d = copy_pack(tmp_path)
    p = d / "summary.md"
    p.write_bytes(p.read_bytes() + b"x" * (1024 * 1024 + 1))
    rehash(d)
    assert ids(load(d).errors) == {"MD-01-SIZE"}
    assert ids(load(FIX / "valid" / DATE, now=BUILT - timedelta(minutes=1)).errors) == {"MD-19-FUTURE-PACK"}


_DET = r"""
import sys, json
sys.path.insert(0, "tests")
from mdpack import FIX, DATE, NOW, SPEC
from optpipe import marketdata as md
out = {c.name: md.load_pack(c / DATE, root=c, now=NOW, spec=SPEC).errors for c in sorted(FIX.iterdir()) if c.is_dir()}
print(json.dumps(out, sort_keys=True))
"""


def test_md21_determinism_across_hashseed_and_box_timezone():
    outs = set()
    for seed, tz in (("0", "UTC"), ("1", "Europe/Berlin"), ("12345", "America/New_York")):
        env = dict(os.environ, PYTHONHASHSEED=seed, TZ=tz, OPTIONS_SPEC_DIR=str(SPEC))
        p = subprocess.run([sys.executable, "-c", _DET], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stderr
        outs.add(p.stdout)
    assert len(outs) == 1
    assert "MD-00-CRASH" not in outs.pop()
