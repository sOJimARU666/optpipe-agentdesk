"""Spec 1.1.7 market_snapshot packs: schema (market_snapshot.schema.json), rules MD-00..MD-12 (market_data_rules.yaml),
validator CLI (validate_market_snapshot.py, exit 0 / 1 / 2) and the reference loader (market_data_loader.py).
Every fixture under fixtures/market_data/ gets a validator test and a loader test; symlinks, executable bits, FIFOs,
oversized files and absolute manifest paths are created at test time in tmp_path. Hermetic: nothing outside this spec
directory and tmp_path is read. All data is fictional EXAMPLE data. PAPER ONLY.
Spec 1.1.8: these 1.1.7 tests run unchanged against the 1.1.7 packs (build_market_data_fixtures_v1_1_7.py,
fixtures/market_data_v1_1_7/); only the id range grew to MD-00..MD-15. The 1.1.8 packs: test_market_snapshot_v1_1_8.py."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import build_market_data_fixtures_v1_1_7 as bmf   # spec 1.1.8: the 1.1.7 builder (unchanged)
import market_data_loader as mdl
import validate_market_snapshot as vms

SPEC = Path(__file__).resolve().parent
FIX = SPEC / "fixtures" / "market_data_v1_1_7"   # spec 1.1.8: the 1.1.7 fixtures moved here (byte-identical)
PY = sys.executable
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
BUILT = datetime.fromisoformat(bmf.BUILT)


def ids(errs):
    return {e.split(" ", 1)[0] for e in errs}


def doc(tk="JPM"):
    return bmf.valid_docs()[f"{tk}.json"]


def check(d, name="JPM.json"):
    return ids(vms.validate_document(d, name))


def cli(*args):
    return subprocess.run([PY, "-B", str(SPEC / "validate_market_snapshot.py"), *map(str, args)], capture_output=True,
                          text=True, env=ENV, cwd=str(SPEC))


def write_pack(root: Path, docs=None, summary=True, extra=None, manifest=True, date=bmf.DATE) -> Path:
    """a pack in root/<date>/ built from `docs` (default: the example), manifest over every file written"""
    d = root / date
    d.mkdir(parents=True)
    files = bmf.pack(docs if docs is not None else bmf.valid_docs(), summary=summary)
    if extra:
        files.update(extra)
    if manifest:
        files = bmf.with_manifest(files)
    for n, b in files.items():
        (d / n).write_bytes(b)
        (d / n).chmod(0o644)
    return d


def remanifest(d: Path):
    files = {p.name: p.read_bytes() for p in d.iterdir() if p.name != "pack.sha256"}
    (d / "pack.sha256").write_bytes(bmf.manifest(files))


def load_ids(d):
    r = mdl.load_pack(d)
    if r.errors:
        assert not r.ok and r.index is None and r.snapshots == {} and r.summary_md is None   # fail closed
    return ids(r.errors), r


# ------------------------------------------------------------------ rules file, schema, ids
def test_staleness_limit_defined_once():
    assert vms.RULES["staleness"]["staleness_limit_minutes"] == 20
    assert vms.STALENESS_LIMIT == timedelta(minutes=20)
    for name in ("validate_market_snapshot.py", "market_data_loader.py"):
        src = (SPEC / name).read_text()
        assert not re.search(r"minutes\s*=\s*20|\b1200\b|\b20\s*\*\s*60\b", src), name
    assert "staleness_limit_minutes" in (SPEC / "market_data_rules.md").read_text()


def test_schema_is_valid_draft_2020_12_and_closed():
    s = json.loads((SPEC / "market_snapshot.schema.json").read_text())
    Draft202012Validator.check_schema(s)

    def walk(x, path=""):
        if isinstance(x, dict):
            if x.get("type") == "object" or (isinstance(x.get("type"), list) and "object" in x["type"]):
                assert x.get("additionalProperties") is False, path
            for k, v in x.items():
                walk(v, f"{path}/{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}/{i}")
    walk(s)


def test_every_emitted_id_is_documented():
    rules = vms.RULES["rules"]
    assert sorted(rules) == [f"MD-{i:02d}" for i in range(0, 16)]   # spec 1.1.8 added MD-13..MD-15
    md = (SPEC / "market_data_rules.md").read_text()
    for name in ("validate_market_snapshot.py", "market_data_loader.py"):
        for full in set(re.findall(r"MD-\d\d-[A-Z]+(?:-[A-Z]+)*", (SPEC / name).read_text())):
            fam, sub = full[:5], full[5:]
            assert fam in rules and sub in rules[fam], full
            assert full in md, full


def test_status_map_into_session_field_status():
    m = vms.RULES["session_status_map"]
    assert set(m) == set(vms.RULES["statuses"]) == {"OK", "STALE", "UNKNOWN", "UNCONFIRMED"}
    assert set(m.values()) <= {"ok", "stale", "missing", "conflicting"} and m["OK"] == "ok"
    assert vms.RULES["usable_statuses"] == ["OK"]


# ------------------------------------------------------------------ vendored fixtures
def test_vendored_fixtures_equal_builder_output():
    built = {f"{case}/{rel}": b for case, files in bmf.build().items() for rel, b in files.items()}
    vendored = {p.relative_to(FIX).as_posix(): p.read_bytes() for p in FIX.rglob("*") if p.is_file()}
    assert vendored == built
    for p in FIX.rglob("*"):
        assert not p.is_symlink() and (p.is_dir() or p.is_file()), p
        if p.is_file():
            assert not os.stat(p).st_mode & 0o111, p


@pytest.mark.parametrize("case", sorted(bmf.EXPECTED))
def test_fixture_loader(case):
    rc, want = bmf.EXPECTED[case]
    got, r = load_ids(FIX / case / bmf.DATE)
    assert want <= got and r.exit_code() == rc, r.errors
    if case == "valid":
        assert r.ok and r.errors == [] and set(r.snapshots) == {"JPM", "WDC", "XMPLA", "XMPLB"}
        assert r.index["date"] == bmf.DATE and r.summary_md.startswith("# EXAMPLE")
    else:
        assert not r.ok


@pytest.mark.parametrize("case", sorted(bmf.EXPECTED))
def test_fixture_validator_cli(case):
    rc, want = bmf.EXPECTED[case]
    out = cli(FIX / case / bmf.DATE)
    assert out.returncode == rc, out.stdout + out.stderr
    for i in want:
        assert i in out.stdout + out.stderr


def test_fixture_specifics():
    assert ids(mdl.load_pack(FIX / "missing_manifest" / bmf.DATE).errors) == {"MD-01-NAME", "MD-02-MISSING"}
    e = mdl.load_pack(FIX / "path_traversal" / bmf.DATE).errors
    assert any("../outside.json" in x and x.startswith("MD-02-PATH") for x in e) and ids(e) == {"MD-02-PATH"}
    e = mdl.load_pack(FIX / "tampered_hash" / bmf.DATE).errors
    assert ids(e) == {"MD-02-HASH"} and all("JPM.json" in x for x in e)
    assert ids(mdl.load_pack(FIX / "stale" / bmf.DATE).errors) == {"MD-05-STALE"}
    assert ids(mdl.load_pack(FIX / "missing_value" / bmf.DATE).errors) == {"MD-06-UNKNOWN"}
    assert ids(mdl.load_pack(FIX / "malformed_json" / bmf.DATE).errors) == {"MD-03-JSON"}


def test_fixture_documents_through_the_cli():
    """single-file mode: document rules only"""
    d = FIX / "valid" / bmf.DATE
    assert cli(*sorted(d.glob("*.json"))).returncode == 0
    assert cli(FIX / "stale" / bmf.DATE / "JPM.json").returncode == 1
    assert cli(FIX / "missing_value" / bmf.DATE / "JPM.json").returncode == 1
    assert cli(FIX / "malformed_json" / bmf.DATE / "WDC.json").returncode == 2


def test_cli_default_mixed_and_missing_paths(tmp_path):
    out = cli()
    assert out.returncode == 0 and "OK:" in out.stdout
    assert cli(FIX / "valid" / bmf.DATE, FIX / "stale" / bmf.DATE).returncode == 1
    assert cli(FIX / "stale" / bmf.DATE, FIX / "malformed_json" / bmf.DATE).returncode == 2   # worst wins
    assert cli(tmp_path / "2026-01-02").returncode == 2                                      # MD-01-DIR
    assert cli(tmp_path / "nope.json").returncode == 2
    (tmp_path / "list.json").write_text("[1, 2]")
    assert cli(tmp_path / "list.json").returncode == 2                                       # not an object
    (tmp_path / "latin.json").write_bytes(b'{"a": "\xff"}')
    assert cli(tmp_path / "latin.json").returncode == 2


def test_example_pack_is_labelled_and_shows_every_status():
    r = mdl.load_pack(FIX / "valid" / bmf.DATE)
    statuses = set()
    for d in [r.index, *r.snapshots.values()]:
        assert d["data_label"] == "EXAMPLE" and d["pack_id"].startswith("EXAMPLE")
    for s in r.snapshots.values():
        statuses |= {f["status"] for _, _, f in vms.iter_fields(s)}
        assert s["iv_rank"]["status"] == "UNKNOWN"                 # Oodle (a): no free IV-rank source, no error
        assert s["last_price"]["delayed"] is True and s["last_price"]["delay_minutes"] == 15
    assert statuses == {"OK", "STALE", "UNKNOWN", "UNCONFIRMED"}
    urls = re.findall(r"https?://([^/\"]+)", json.dumps(r.index) + json.dumps(r.snapshots))
    assert urls and all(u.endswith(("example.com", "example.org")) for u in urls)
    assert r.snapshots["XMPLB"]["watchlist"] == "street" and r.snapshots["XMPLB"]["candidate_status"] == "idea"
    assert "candidate_status" not in r.snapshots["XMPLA"] and mdl.vms.candidate_status(r.snapshots["XMPLA"]) == "idea"


# ------------------------------------------------------------------ MD-05 staleness boundaries
@pytest.mark.parametrize("age,stale", [
    (timedelta(0), False), (timedelta(minutes=19, seconds=59), False), (timedelta(minutes=20), False),
    (timedelta(minutes=20, microseconds=1000), True), (timedelta(minutes=20, seconds=1), True),
    (timedelta(minutes=21), True), (timedelta(hours=17), True)])
@pytest.mark.parametrize("tz", [timezone.utc, timezone(timedelta(hours=1)), timezone(timedelta(hours=-5)), None])
def test_md05_boundary_any_offset(age, stale, tz):
    """exactly 20:00 old is OK (inclusive limit, the R01 / W06 convention); 20:00.001 and 20:01 are STALE; the same
    instant written with Z / +01:00 / -05:00 / the pack's -04:00 gives the same answer"""
    t = BUILT - age
    s = (t.astimezone(tz) if tz else t).isoformat()
    for path in ("last_price", "iv"):
        d = doc()
        f = d[path]
        f.update(as_of=s, status="OK")
        assert ("MD-05-STALE" in check(d)) == stale, (path, s)
        f["status"] = "STALE"
        assert "MD-05-STALE" not in check(d)                     # STALE may be marked early (conservative), never late


def test_md05_contract_and_summary_fields():
    d = doc()
    d["option_liquidity"]["contracts"][0]["open_interest"].update(status="OK")      # prior-session chain marked OK
    assert "MD-05-STALE" in check(d)
    d = doc("WDC")
    d["option_liquidity"]["summary"]["spread_pct_of_mid"].update(status="OK")
    assert "MD-05-STALE" in check(d, "WDC.json")


def test_md05_not_for_earnings():
    d = doc()
    d["earnings_date"]["as_of"] = d["earnings_date"]["sources"][0]["as_of"] = "2026-10-01T08:00:00-04:00"
    assert check(d) == set()


# ------------------------------------------------------------------ MD-04 times
def test_md04_times():
    d = doc(); d["pack_built_at"] = "2026-10-20T09:00:00"
    assert "MD-04-TIME-NAIVE" in check(d)
    d = doc(); d["last_price"]["as_of"] = "2026-10-20T08:50:00"
    assert check(d) == {"MD-04-TIME-NAIVE"}
    d = doc(); d["last_price"]["as_of"] = "20 Oct 2026 08:50"
    assert check(d) == {"MD-04-TIME-INVALID"}
    d = doc(); d["last_price"]["as_of"] = "2026-10-20T09:00:01-04:00"
    assert check(d) == {"MD-04-FUTURE"}
    d = doc(); d["last_price"]["as_of"] = "2026-10-20T13:00:00Z"                     # = pack_built_at: fine
    assert check(d) == set()
    d = doc(); d["pack_built_at"] = "2026-10-20T13:00:00Z"
    assert check(d) == set()


# ------------------------------------------------------------------ MD-06 unknown
def test_md06_unknown():
    d = doc(); d["last_price"]["value"] = None
    assert check(d) == {"MD-06-UNKNOWN"}
    d = doc(); del d["last_price"]["value"]
    assert check(d) == {"MD-06-UNKNOWN"}
    d = doc(); d["iv"].update(value=None, status="STALE")
    assert check(d) == {"MD-06-UNKNOWN"}
    d = doc(); d["last_price"].update(value=None, status="UNKNOWN")
    assert check(d) == set()                                                          # source / as_of may stay
    d = doc(); d["last_price"]["status"] = "UNKNOWN"
    assert check(d) == {"MD-06-VALUE"}
    d = doc(); d["last_price"]["source"] = None
    assert check(d) == {"MD-06-UNSOURCED"}
    d = doc(); d["last_price"]["as_of"] = None
    assert check(d) == {"MD-06-UNSOURCED"}
    d = doc(); d["earnings_time"]["value"] = "unknown"
    assert check(d) == {"MD-06-UNKNOWN"}
    d = doc("XMPLA")                                                                   # iv_rank / iv / earnings UNKNOWN
    assert check(d, "XMPLA.json") == set()


# ------------------------------------------------------------------ MD-07 earnings
def test_md07_earnings_two_distinct_domains():
    d = doc()
    assert check(d) == set()
    d["earnings_date"]["sources"] = d["earnings_date"]["sources"][:1]
    assert check(d) == {"MD-07-UNCONFIRMED"}
    d["earnings_date"]["status"] = "UNCONFIRMED"
    assert check(d) == set()
    d = doc(); d["earnings_date"]["sources"][1]["url"] = "https://www.example.com/other"    # same domain twice
    assert check(d) == {"MD-07-UNCONFIRMED"}
    d = doc(); d["earnings_date"]["sources"][1]["url"] = "https://news.ir.EXAMPLE.com./x"
    assert check(d) == {"MD-07-UNCONFIRMED"}
    d = doc(); del d["earnings_time"]["sources"]
    assert check(d) == {"MD-07-UNCONFIRMED"}
    d = doc(); d["iv"]["status"] = "UNCONFIRMED"
    assert check(d) == {"MD-07-STATUS"}
    assert vms.registrable_domain("https://www.example.org/wire") == "example.org"
    assert vms.registrable_domain("https://localhost/x") is None


# ------------------------------------------------------------------ MD-08 delay
def test_md08_delay():
    d = doc(); del d["last_price"]["delay_minutes"]
    assert check(d) == {"MD-08-DELAY"}
    d = doc(); d["iv"]["delay_minutes"] = 15
    assert check(d) == {"MD-08-DELAY"}
    d = doc(); d["last_price"]["delay_minutes"] = 0
    assert "MD-03-SCHEMA" in check(d)


# ------------------------------------------------------------------ MD-09 privacy / MD-03 closed schema
def test_md09_no_email_anywhere(tmp_path):
    d = doc(); d["last_price"]["note"] = "contact someone@example.com"
    assert check(d) == {"MD-09-EMAIL"}                                                # also placeholder domains
    docs = bmf.valid_docs(); docs["index.json"]["sources_terms"][0]["note"] = "UA contact ops@example.net"
    assert "MD-09-EMAIL" in ids(mdl.load_pack(write_pack(tmp_path / "a", docs)).errors)
    d = write_pack(tmp_path / "b")
    (d / "summary.md").write_text("ask someone@example.org\n"); remanifest(d)
    assert ids(mdl.load_pack(d).errors) == {"MD-09-EMAIL"}


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(account_id="U123"), lambda d: d.update(email="EXAMPLE"), lambda d: d.update(user="eugene"),
    lambda d: d["last_price"].update(account="x"), lambda d: d.update(watchlist="mine"),
    lambda d: d["iv_rank"].update(value=101, status="OK"), lambda d: d["last_price"].update(value=-1),
    lambda d: d.update(schema_version="1.1.6"), lambda d: d.update(data_label="REAL"),
    lambda d: d["earnings_time"].update(value="DMH"), lambda d: d["last_price"].update(status="ok"),
    lambda d: d["last_price"].update(delayed="yes"), lambda d: d.update(option_liquidity={}),
    lambda d: d["option_liquidity"]["contracts"][0].update(position=1), lambda d: d.pop("iv_rank"),
    lambda d: d["earnings_date"]["sources"][0].update(url="mailto:EXAMPLE"),
    lambda d: d.update(kind="snapshot")])
def test_md03_schema_closed(mutate):
    d = doc(); mutate(d)
    assert "MD-03-SCHEMA" in check(d)


def test_md03_not_an_object_and_never_raises():
    for bad in ([], "x", 1, None, {"kind": "ticker_snapshot", "last_price": "abc", "option_liquidity": {"contracts": [1]}},
                {"kind": "pack_index", "tickers": "x"}, {"kind": "ticker_snapshot", "pack_built_at": 5, "iv": {"as_of": 7}}):
        e = vms.validate_document(bad, "x.json")
        assert e and ids(e) <= {"MD-03-JSON", "MD-03-SCHEMA", "MD-04-TIME-INVALID", "MD-06-UNKNOWN", "MD-06-UNSOURCED"}, \
            (bad, e)


def test_iv_rank_unknown_is_normal():
    d = doc()
    assert d["iv_rank"] == {"value": None, "source": None, "as_of": None, "delayed": False, "status": "UNKNOWN",
                            "note": "EXAMPLE: no free IV-rank source"} and check(d) == set()


# ------------------------------------------------------------------ MD-10 street
def test_md10_street_is_idea_only(tmp_path):
    d = doc("XMPLB"); d["candidate_status"] = "candidate"
    assert check(d, "XMPLB.json") == {"MD-10-STREET-CANDIDATE"}
    d = doc("XMPLB"); del d["candidate_status"]
    assert check(d, "XMPLB.json") == set()                                           # default idea
    d = doc("XMPLA"); d["candidate_status"] = "candidate"                             # owner list may carry it
    assert check(d, "XMPLA.json") == set()
    docs = bmf.valid_docs(); docs["index.json"]["tickers"][3]["candidate_status"] = "candidate"
    got = ids(mdl.load_pack(write_pack(tmp_path, docs)).errors)
    assert {"MD-10-STREET-CANDIDATE", "MD-11-TICKER"} == got


# ------------------------------------------------------------------ MD-11 / MD-12 / MD-01-DATE (pack level)
def _pack_ids(tmp_path, docs, **kw):
    return ids(mdl.load_pack(write_pack(tmp_path, docs, **kw)).errors)


def test_md11_consistency(tmp_path):
    docs = bmf.valid_docs(); docs["JPM.json"]["pack_id"] = "EXAMPLE-other"
    assert _pack_ids(tmp_path / "a", docs) == {"MD-11-PACK"}
    docs = bmf.valid_docs(); docs["JPM.json"]["watchlist"] = "eugene"
    assert _pack_ids(tmp_path / "b", docs) == {"MD-11-TICKER"}
    docs = bmf.valid_docs(); docs["JPM.json"]["ticker"] = "JPMX"
    assert _pack_ids(tmp_path / "c", docs) == {"MD-11-TICKER"}
    docs = bmf.valid_docs(); docs["index.json"]["tickers"].append(dict(docs["index.json"]["tickers"][0]))
    assert _pack_ids(tmp_path / "d", docs) == {"MD-11-DUPLICATE"}
    docs = bmf.valid_docs(); docs["index.json"]["tickers"][0]["file"] = "WDC.json"
    assert "MD-11-TICKER" in _pack_ids(tmp_path / "e", docs)


def test_md12_sources_terms(tmp_path):
    docs = bmf.valid_docs(); docs["index.json"]["sources_terms"].pop(3)               # example_wire unlisted
    assert _pack_ids(tmp_path / "a", docs) == {"MD-12-SOURCE"}
    docs = bmf.valid_docs(); del docs["index.json"]["sources_terms"]                  # optional
    assert _pack_ids(tmp_path / "b", docs) == set()
    docs = bmf.valid_docs(); docs["index.json"]["sources_terms"][0]["basis"] = "scraped"
    assert _pack_ids(tmp_path / "c", docs) == {"MD-03-SCHEMA"}
    docs = bmf.valid_docs(); docs["index.json"]["sources_terms"][0]["contact"] = "EXAMPLE"
    assert _pack_ids(tmp_path / "d", docs) == {"MD-03-SCHEMA"}


def _shifted(delta: timedelta):
    """the example pack with pack_built_at and every as_of moved by `delta` (same ages, same offsets)"""
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("as_of", "pack_built_at") and isinstance(v, str):
                    x[k] = (datetime.fromisoformat(v) + delta).isoformat()
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    docs = bmf.valid_docs()
    walk(docs)
    return docs


def test_md01_date_is_the_new_york_date(tmp_path):
    late = _shifted(timedelta(hours=14, minutes=59, seconds=59))                      # built 23:59:59 NY on 10-20
    assert late["index.json"]["pack_built_at"] == "2026-10-20T23:59:59-04:00"
    assert _pack_ids(tmp_path / "a", late) == set()
    for d in late.values():
        d["pack_built_at"] = "2026-10-21T03:59:59+00:00"                              # same instant, UTC
    assert _pack_ids(tmp_path / "a2", late) == set()
    assert _pack_ids(tmp_path / "b", _shifted(timedelta(hours=15))) == {"MD-01-DATE"}     # 00:00 NY on 10-21
    docs = bmf.valid_docs(); docs["index.json"]["date"] = "2026-10-19"
    assert _pack_ids(tmp_path / "c", docs) == {"MD-01-DATE"}
    assert _pack_ids(tmp_path / "d", bmf.valid_docs(), date="2026-10-21") == {"MD-01-DATE"}


# ------------------------------------------------------------------ MD-01 / MD-02 layout and manifest (loader)
def test_summary_is_optional_but_listed(tmp_path):
    assert _pack_ids(tmp_path / "a", bmf.valid_docs(), summary=False) == set()
    d = write_pack(tmp_path / "b")
    lines = [l for l in (d / "pack.sha256").read_text().splitlines() if not l.endswith("summary.md")]
    (d / "pack.sha256").write_text("\n".join(lines) + "\n")
    assert ids(mdl.load_pack(d).errors) == {"MD-02-EXTRA"}


def test_md02_manifest_rules(tmp_path):
    def variant(name, edit):
        d = write_pack(tmp_path / name)
        m = d / "pack.sha256"
        m.write_text(edit(m.read_text()))
        return ids(mdl.load_pack(d).errors)
    h = "0" * 64
    assert variant("abs", lambda t: t + f"{h}  /etc/passwd\n") == {"MD-02-PATH"}
    assert variant("sub", lambda t: t + f"{h}  sub/JPM.json\n") == {"MD-02-PATH"}
    assert variant("bs", lambda t: t + f"{h}  ..\\JPM.json\n") == {"MD-02-PATH"}
    assert variant("dot", lambda t: t + f"{h}  .hidden\n") == {"MD-02-PATH"}
    assert variant("dup", lambda t: t + t.splitlines()[0] + "\n") == {"MD-02-DUPLICATE"}
    assert variant("fmt", lambda t: t.replace("  ", " ", 1)) == {"MD-02-FORMAT", "MD-02-EXTRA", "MD-02-INDEX"}
    assert variant("upper", lambda t: t[:1].upper() + t[1:] if t[0].isalpha() else "A" + t[1:]) >= {"MD-02-FORMAT"}
    assert variant("self", lambda t: t + f"{h}  pack.sha256\n") == {"MD-02-FORMAT"}
    assert variant("empty", lambda t: "") >= {"MD-02-FORMAT", "MD-02-EXTRA"}
    assert variant("absent", lambda t: t + f"{h}  AAA.json\n") == {"MD-02-ABSENT", "MD-02-INDEX"}
    assert variant("badname", lambda t: t + f"{h}  run.py\n") == {"MD-01-NAME"}
    assert variant("idx", lambda t: "".join(l + "\n" for l in t.splitlines() if not l.endswith("index.json"))) == \
        {"MD-02-EXTRA", "MD-02-INDEX"}


def test_md02_extra_and_index_mismatch(tmp_path):
    d = write_pack(tmp_path / "a")
    (d / "AAA.json").write_bytes((d / "JPM.json").read_bytes())
    assert ids(mdl.load_pack(d).errors) == {"MD-02-EXTRA"}
    remanifest(d)                                                                     # listed, but not in index
    assert "MD-02-INDEX" in ids(mdl.load_pack(d).errors)
    docs = bmf.valid_docs(); del docs["WDC.json"]                                    # index names a missing file
    assert _pack_ids(tmp_path / "b", docs) == {"MD-02-INDEX"}


def test_md01_names_scripts_dirs_dotfiles(tmp_path):
    for i, (name, kind) in enumerate([("refresh_quotes.py", "f"), ("static.json", "f"), ("notes.md", "f"),
                                      (".DS_Store", "f"), ("__pycache__", "d"), ("run.sh", "f"), ("JPM.json.bak", "f")]):
        d = write_pack(tmp_path / str(i))
        (d / name).mkdir() if kind == "d" else (d / name).write_text("x")
        assert "MD-01-NAME" in ids(mdl.load_pack(d).errors), name
        if kind == "f":
            remanifest(d)                                                             # listing it does not help
            assert "MD-01-NAME" in ids(mdl.load_pack(d).errors), name


def test_md01_executable_bit(tmp_path):
    d = write_pack(tmp_path)
    (d / "JPM.json").chmod(0o755)
    assert ids(mdl.load_pack(d).errors) == {"MD-01-EXEC"}
    assert cli(d).returncode == 1


def test_md01_fifo_is_never_opened(tmp_path):
    d = write_pack(tmp_path)
    os.mkfifo(d / "AAA.json")                                                         # opening it would block
    assert ids(mdl.load_pack(d).errors) == {"MD-01-NOT-REGULAR", "MD-02-EXTRA"}
    h = "0" * 64
    with open(d / "pack.sha256", "a") as f:
        f.write(f"{h}  AAA.json\n")
    assert "MD-01-NOT-REGULAR" in ids(mdl.load_pack(d).errors)


def test_md01_oversized_file_not_read(tmp_path):
    d = write_pack(tmp_path)
    (d / "summary.md").write_bytes(b"# EXAMPLE\n" + b"x" * mdl.MAX_BYTES); remanifest(d)
    assert ids(mdl.load_pack(d).errors) == {"MD-01-SIZE"}


def test_md01_symlinked_file(tmp_path):
    d = write_pack(tmp_path / "root")
    real = tmp_path / "elsewhere.json"
    real.write_bytes((d / "JPM.json").read_bytes())
    (d / "JPM.json").unlink(); (d / "JPM.json").symlink_to(real)                      # same bytes, manifest matches
    e, r = load_ids(d)
    assert "MD-01-SYMLINK" in e and not r.ok and "MD-01-UNREADABLE" not in e and r.exit_code() == 1
    assert cli(d).returncode == 1


def test_md01_symlink_to_outside_target_listed(tmp_path):
    d = write_pack(tmp_path / "root")
    (tmp_path / "secret.txt").write_text("not for the app")
    (d / "AAA.json").symlink_to(tmp_path / "secret.txt"); remanifest(d)              # remanifest reads through it
    assert "MD-01-SYMLINK" in ids(mdl.load_pack(d).errors)


def test_md01_symlinked_date_dir_and_ancestor(tmp_path):
    real = write_pack(tmp_path / "real")
    link_root = tmp_path / "linkroot"; link_root.mkdir()
    (link_root / bmf.DATE).symlink_to(real, target_is_directory=True)
    e, r = load_ids(link_root / bmf.DATE)
    assert e == {"MD-01-SYMLINK"} and r.exit_code() == 1
    (tmp_path / "anc").symlink_to(tmp_path / "real", target_is_directory=True)
    assert ids(mdl.load_pack(tmp_path / "anc" / bmf.DATE).errors) == {"MD-01-SYMLINK"}
    assert ids(mdl.load_pack(tmp_path / "real" / bmf.DATE).errors) == set()
    assert cli(link_root / bmf.DATE).returncode == 1


def test_md01_dir_missing_or_not_a_dir(tmp_path):
    r = mdl.load_pack(tmp_path / "2026-10-20")
    assert ids(r.errors) == {"MD-01-DIR"} and r.exit_code() == 2
    (tmp_path / "2026-10-21").write_text("x")
    assert ids(mdl.load_pack(tmp_path / "2026-10-21").errors) == {"MD-01-DIR"}


def test_missing_manifest_like_a_non_conformant_pack(tmp_path):
    """the shape of a non-conformant TEST pack (no manifest, a script, extra .json / .md): fail closed, nothing loaded"""
    d = write_pack(tmp_path, manifest=False, extra={"refresh_quotes.py": b"x\n", "static.json": b"{}\n",
                                                    "sources-and-terms.md": b"# EXAMPLE\n"})
    os.mkdir(d / "__pycache__")
    e, r = load_ids(d)
    assert e == {"MD-02-MISSING", "MD-01-NAME"} and not r.ok and r.snapshots == {}


# ------------------------------------------------------------------ fail closed, read-only, no network
def test_crash_is_md00_and_exit_2(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("EXAMPLE crash")
    monkeypatch.setattr(mdl.vms, "validate_document", boom)
    r = mdl.load_pack(FIX / "valid" / bmf.DATE)
    assert ids(r.errors) == {"MD-00-CRASH"} and not r.ok and r.snapshots == {} and r.exit_code() == 2
    assert vms.main([str(FIX / "valid" / bmf.DATE / "JPM.json")]) == 2


def test_loader_is_read_only(tmp_path):
    d = write_pack(tmp_path)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in d.iterdir()}
    for _ in range(2):
        assert mdl.load_pack(d).ok
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in d.iterdir()} == before


def test_usable_fields_fail_closed():
    r = mdl.load_pack(FIX / "valid" / bmf.DATE)
    jpm, wdc, xa = r.snapshots["JPM"], r.snapshots["WDC"], r.snapshots["XMPLA"]
    assert mdl.usable_value(jpm["last_price"]) == 101.25 and mdl.field_usable(jpm["earnings_date"])
    assert mdl.usable_value(jpm["iv"]) is None                                       # STALE
    assert mdl.usable_value(wdc["earnings_date"]) is None                            # UNCONFIRMED
    assert mdl.usable_value(xa["iv_rank"]) is None and not mdl.field_usable(None)    # UNKNOWN / garbage


BANNED = {"socket", "http", "urllib.request", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "subprocess",
          "shutil", "tempfile", "ssl"}


@pytest.mark.parametrize("name", ["validate_market_snapshot.py", "market_data_loader.py"])
def test_no_network_no_writes(name):
    tree = ast.parse((SPEC / name).read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
    assert not {m for m in mods if m in BANNED or m.split(".")[0] in {"socket", "http", "requests", "httpx"}}, mods
    src = (SPEC / name).read_text()
    for bad in ("write_text", "write_bytes", "O_WRONLY", "O_RDWR", "O_CREAT", "unlink", "rmtree", "urlopen",
                '"w"', "'w'", '"a"', "'a'"):
        assert bad not in src, (name, bad)
