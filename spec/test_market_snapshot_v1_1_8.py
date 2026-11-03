"""Spec 1.1.8 market_snapshot packs (schema_version "1.1.8"): robots_only_no_terms, sources_not_used, per-field
as_of_basis (MD-13), declared source delay (MD-14), earnings independence by origin (MD-07, MD-07-ORIGIN),
MD-12-CONFLICT, catalysts.json (MD-15, MD-11-PACK / MD-12-SOURCE / MD-04 on it) and the loader whitelist.
The 1.1.7 packs keep their own tests (test_market_snapshot_v1_1_7.py) and the frozen-release proof
(test_release_v1_1_8.py). Every fixture under fixtures/market_data/ gets a loader and a CLI test. Hermetic: nothing
outside this spec directory and tmp_path is read. All data is fictional EXAMPLE data. PAPER ONLY."""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import build_market_data_fixtures as bmf
import build_market_data_fixtures_v1_1_7 as b117
import market_data_loader as mdl
import validate_market_snapshot as vms

SPEC = Path(__file__).resolve().parent
FIX = SPEC / "fixtures" / "market_data"
PY = sys.executable
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
SCHEMA = json.loads((SPEC / "market_snapshot.schema.json").read_text())
DEFS = SCHEMA["$defs"]


def ids(errs):
    return {e.split(" ", 1)[0] for e in errs}


def docs():
    return bmf.valid_docs()


def check(d, name):
    return ids(vms.validate_document(d, name))


def cli(*args):
    return subprocess.run([PY, "-B", str(SPEC / "validate_market_snapshot.py"), *map(str, args)], capture_output=True,
                          text=True, env=ENV, cwd=str(SPEC))


def write_pack(root: Path, d=None, summary=True, extra=None, manifest=True, builder=bmf) -> Path:
    p = root / bmf.DATE
    p.mkdir(parents=True)
    files = builder.pack(d if d is not None else builder.valid_docs(), summary=summary)
    if extra:
        files.update(extra)
    if manifest:
        files = builder.with_manifest(files)
    for n, b in files.items():
        (p / n).write_bytes(b)
        (p / n).chmod(0o644)
    return p


def pack_ids(root, d, **kw):
    return ids(mdl.load_pack(write_pack(root, d, **kw)).errors)


def src(source, url, origin, entity):
    return bmf.src(source, url, origin, entity)


# ------------------------------------------------------------------ rules file, schema, ids
def test_rules_versions_and_new_families():
    r = vms.RULES
    assert r["market_data_rules_version"] == "1.1.8" and r["schema_versions"] == ["1.1.7", "1.1.8"]
    assert sorted(r["rules"]) == [f"MD-{i:02d}" for i in range(0, 16)]
    for fam, subs in {"MD-07": ["-ORIGIN"], "MD-12": ["-CONFLICT"], "MD-13": ["-MISSING", "-DELAY"],
                      "MD-14": ["-DELAY"],
                      "MD-15": ["-UNKNOWN", "-VALUE", "-UNSOURCED", "-UNCONFIRMED", "-TIME-NAIVE", "-TIME-INVALID",
                                "-DATE", "-DUPLICATE"]}.items():
        for s in subs:
            assert s in r["rules"][fam], (fam, s)
            assert f"{fam}{s}" in (SPEC / "market_data_rules.md").read_text(), (fam, s)


def test_yaml_and_schema_enums_agree():
    r = vms.RULES
    assert DEFS["PriceFieldV118"]["properties"]["as_of_basis"]["enum"] == r["as_of_basis"]["values"] == [
        "quote_time", "last_trade", "fetch_minus_delay", "published"]
    for d in ("PriceFieldV118", "NonNegFieldV118", "CountFieldV118", "IvRankFieldV118", "EarningsDateFieldV118",
              "EarningsTimeFieldV118"):
        assert DEFS[d]["properties"]["as_of_basis"]["enum"] == r["as_of_basis"]["values"], d
    st = DEFS["PackIndexV118"]["properties"]["sources_terms"]["items"]["properties"]
    assert st["basis"]["enum"] == r["source_terms_bases"] and "robots_only_no_terms" in st["basis"]["enum"]
    snu = DEFS["PackIndexV118"]["properties"]["sources_not_used"]["items"]["properties"]
    assert snu["reason"]["enum"] == r["sources_not_used_reasons"] == [
        "terms_forbid", "login_required", "paid", "contact_email_required", "other"]
    assert DEFS["SourceRefV118"]["properties"]["origin"]["enum"] == r["earnings"]["origins"] == sorted(vms.ORIGINS, key=r["earnings"]["origins"].index)
    assert DEFS["CatalystEntry"]["properties"]["catalyst_type"]["enum"] == r["catalysts"]["types"]
    assert DEFS["CatalystEntry"]["properties"]["status"]["enum"] == r["catalysts"]["statuses"] == [
        "CONFIRMED", "UNCONFIRMED", "UNKNOWN"]
    assert DEFS["AnalystAction"]["properties"]["action"]["enum"] == r["catalysts"]["analyst_actions"]
    assert r["layout"]["catalysts"] == r["catalysts"]["file"] == mdl.CATALYSTS == "catalysts.json"
    assert set(r["earnings"]["issuer_channel_domains"]) >= {"globenewswire.com", "prnewswire.com", "businesswire.com",
                                                            "gcs-web.com"}
    assert r["earnings"]["independence_v1_1_8"] == "origin"
    assert vms.AS_OF_BASIS_REQUIRED == set(r["as_of_basis"]["required_for"])
    assert {"last_price", "iv", "implied_move", "open_interest", "volume", "bid", "ask", "spread",
            "spread_pct_of_mid", "prior_close"} == vms.AS_OF_BASIS_REQUIRED


def test_1_1_7_defs_unchanged_and_v118_defs_pinned():
    for k in ("TickerSnapshot", "PackIndex"):
        assert DEFS[k]["properties"]["schema_version"]["enum"] == ["1.1.7"]
    for k in ("TickerSnapshotV118", "PackIndexV118", "CatalystLog"):
        assert DEFS[k]["properties"]["schema_version"]["enum"] == ["1.1.8"]
    assert "as_of_basis" not in json.dumps({k: v for k, v in DEFS.items() if not k.endswith("V118")
                                            and k not in ("CatalystLog", "CatalystEntry", "TickerContext", "AnalystAction")})
    Draft202012Validator.check_schema(SCHEMA)


# ------------------------------------------------------------------ vendored fixtures
def test_vendored_fixtures_equal_builder_output():
    built = {f"{case}/{rel}": b for case, files in bmf.build().items() for rel, b in files.items()}
    vendored = {p.relative_to(FIX).as_posix(): p.read_bytes() for p in FIX.rglob("*") if p.is_file()}
    assert vendored == built
    assert set(bmf.EXPECTED) == set(bmf.build())
    for p in FIX.rglob("*"):
        assert not p.is_symlink(), p
        if p.is_file():
            assert p.stat().st_mode & 0o777 == 0o644, p


@pytest.mark.parametrize("case", sorted(bmf.EXPECTED))
def test_fixture_case_loader(case):
    rc, want = bmf.EXPECTED[case]
    r = mdl.load_pack(FIX / case / bmf.DATE)
    got = ids(r.errors)
    assert want <= got and r.exit_code() == rc, (case, r.errors)
    if rc == 0:
        assert r.ok and r.catalysts is not None and r.index["schema_version"] == "1.1.8"
    else:
        assert not r.ok and r.index is None and r.snapshots == {} and r.catalysts is None and r.summary_md is None


@pytest.mark.parametrize("case", sorted(bmf.EXPECTED))
def test_fixture_case_cli(case):
    rc, want = bmf.EXPECTED[case]
    out = cli(FIX / case / bmf.DATE)
    assert out.returncode == rc, out.stdout + out.stderr
    for i in want:
        assert i in out.stdout + out.stderr, (case, i)


def test_fixture_cases_exact():
    exact = {"valid": set(), "missing_basis": {"MD-13-MISSING"}, "undeclared_delay": {"MD-14-DELAY"},
             "earnings_same_origin": {"MD-07-UNCONFIRMED"},
             "catalysts_bad": {"MD-15-UNCONFIRMED", "MD-15-UNKNOWN", "MD-15-TIME-NAIVE"},
             "catalysts_in_1_1_7": {"MD-01-NAME", "MD-02-EXTRA"}}
    for case, want in exact.items():
        assert ids(mdl.load_pack(FIX / case / bmf.DATE).errors) == want, case


def test_cli_ok_message_names_md15():
    out = cli(FIX / "valid" / bmf.DATE)
    assert out.returncode == 0 and "market_snapshot 1.1.8 rules" in out.stdout and "MD-00..MD-15" in out.stdout
    assert "schema_version 1.1.8," in out.stdout
    old = cli(SPEC / "fixtures" / "market_data_v1_1_7" / "valid" / bmf.DATE)   # the OK line names the pack's own version
    assert old.returncode == 0 and "schema_version 1.1.7," in old.stdout
    assert cli().returncode == 0                                           # no PATH: the example pack
    assert cli(FIX / "valid" / bmf.DATE / "catalysts.json").returncode == 0
    assert cli(FIX / "catalysts_bad" / bmf.DATE / "catalysts.json").returncode == 1


def test_example_pack_fixes_and_shows_the_new_fields():
    r = mdl.load_pack(FIX / "valid" / bmf.DATE)
    assert r.ok
    statuses = set()
    declared = {s["source"]: s for s in r.index["sources_terms"]}
    for s in r.snapshots.values():
        assert s["schema_version"] == "1.1.8"
        for path, leaf, f in vms.iter_fields(s):
            statuses.add(f["status"])
            if f["value"] is not None and leaf in vms.AS_OF_BASIS_REQUIRED:
                assert f["as_of_basis"] in vms.RULES["as_of_basis"]["values"], path
            if f["value"] is not None and declared.get(f["source"], {}).get("delayed"):
                assert f["delayed"] is True and f["delay_minutes"] >= declared[f["source"]]["delay_minutes"], path
            if f["source"] == "example_chain":
                assert f["delayed"] is True and f["delay_minutes"] == 15 and f["as_of_basis"] == "last_trade"
    assert statuses == {"OK", "STALE", "UNKNOWN", "UNCONFIRMED"}
    robots = [s for s in r.index["sources_terms"] if s["basis"] == "robots_only_no_terms"]
    assert robots and all("terms_url" not in s for s in robots)
    assert {s["reason"] for s in r.index["sources_not_used"]} == {"login_required", "contact_email_required"}
    jpm = r.snapshots["JPM"]["earnings_date"]
    assert jpm["status"] == "OK" and len(vms._origin_pairs(jpm["sources"])) == 2
    assert r.snapshots["WDC"]["earnings_date"]["status"] == "UNCONFIRMED"
    cat = r.catalysts
    assert {c["status"] for c in cat["catalysts"]} == {"CONFIRMED", "UNCONFIRMED", "UNKNOWN"}
    assert any(c["ticker"] is None for c in cat["catalysts"])
    assert cat["ticker_context"][0]["prior_close"]["status"] == "OK"     # a prior close is never STALE
    blob = json.dumps([r.index, r.snapshots, r.catalysts]) + r.summary_md
    assert not vms.EMAIL.search(blob)
    urls = re.findall(r"https?://([^/\"]+)", blob)
    assert urls and all(u.endswith(("example.com", "example.org")) for u in urls)
    assert "call time" in r.summary_md or "conference-call time" in r.summary_md


# ------------------------------------------------------------------ robots_only_no_terms, sources_not_used, MD-12-CONFLICT
def test_robots_only_no_terms_and_declared_delay_shape():
    d = docs(); idx = d["index.json"]
    assert check(idx, "index.json") == set()
    i = copy.deepcopy(idx); i["sources_terms"][5]["terms_url"] = "https://screener.example.org/terms"
    assert check(i, "index.json") == {"MD-03-SCHEMA"}
    i = copy.deepcopy(idx); del i["sources_terms"][0]["delay_minutes"]
    assert check(i, "index.json") == {"MD-03-SCHEMA"}
    i = copy.deepcopy(idx); i["sources_terms"][2]["delay_minutes"] = 5            # delay_minutes without delayed
    assert check(i, "index.json") == {"MD-03-SCHEMA"}
    i = copy.deepcopy(idx); i["sources_terms"][2].update(delayed=False, delay_minutes=5)
    assert check(i, "index.json") == {"MD-03-SCHEMA"}
    i = copy.deepcopy(idx); i["sources_terms"][0]["delay_minutes"] = 0
    assert check(i, "index.json") == {"MD-03-SCHEMA"}


def test_sources_not_used():
    idx = docs()["index.json"]
    i = copy.deepcopy(idx); del i["sources_not_used"]
    assert check(i, "index.json") == set()                                          # optional
    i = copy.deepcopy(idx); i["sources_not_used"][0]["reason"] = "too_slow"
    assert check(i, "index.json") == {"MD-03-SCHEMA"}
    i = copy.deepcopy(idx); i["sources_not_used"].append({"name": "example_x", "domain": "example.com", "reason": "other"})
    assert check(i, "index.json") == {"MD-03-SCHEMA"}                               # other needs a note
    i["sources_not_used"][-1]["note"] = "EXAMPLE: rate limited"
    assert check(i, "index.json") == set()
    i = copy.deepcopy(idx); i["sources_not_used"][1]["note"] = "EXAMPLE: asks for data@example.com"
    assert check(i, "index.json") == {"MD-09-EMAIL"}                                # MD-09: not even example.com
    i = copy.deepcopy(idx); i["sources_not_used"][0]["contact"] = "EXAMPLE"
    assert check(i, "index.json") == {"MD-03-SCHEMA"}
    i = copy.deepcopy(idx); i["sources_not_used"][0]["domain"] = "ops@example.com"
    assert "MD-09-EMAIL" in check(i, "index.json")


def test_md12_conflict(tmp_path):
    d = docs(); d["index.json"]["sources_not_used"][0]["name"] = "example_wire"
    assert check(d["index.json"], "index.json") == {"MD-12-CONFLICT"}
    assert pack_ids(tmp_path, d) == {"MD-12-CONFLICT"}


# ------------------------------------------------------------------ MD-13 as_of_basis
@pytest.mark.parametrize("path", [("last_price",), ("iv",), ("implied_move",),
                                  ("option_liquidity", "contracts", 0, "bid"), ("option_liquidity", "contracts", 0, "ask"),
                                  ("option_liquidity", "contracts", 0, "open_interest"),
                                  ("option_liquidity", "contracts", 0, "volume"),
                                  ("option_liquidity", "contracts", 0, "spread"),
                                  ("option_liquidity", "contracts", 0, "spread_pct_of_mid")])
def test_md13_missing_on_price_and_chain_fields(path):
    d = docs()["JPM.json"]
    f = d
    for k in path:
        f = f[k]
    del f["as_of_basis"]
    assert check(d, "JPM.json") == {"MD-13-MISSING"}


def test_md13_summary_fields_and_exemptions():
    d = docs()["WDC.json"]; del d["option_liquidity"]["summary"]["open_interest"]["as_of_basis"]
    assert check(d, "WDC.json") == {"MD-13-MISSING"}
    d = docs()["JPM.json"]
    for k in ("earnings_date", "earnings_time"):
        d[k].pop("as_of_basis", None)                                               # optional on earnings
    assert check(d, "JPM.json") == set()
    d = docs()["XMPLA.json"]                                                        # UNKNOWN fields carry none
    assert "as_of_basis" not in d["iv"] and check(d, "XMPLA.json") == set()
    d = docs()["JPM.json"]; d["iv_rank"] = bmf.F(40.0, "example_chain", bmf.CHAIN_AS_OF, status="STALE", delayed=True,
                                                 delay_minutes=15)
    assert check(d, "JPM.json") == set()                                            # iv_rank: optional
    d = docs()["JPM.json"]; d["last_price"]["as_of_basis"] = "guess"
    assert check(d, "JPM.json") == {"MD-03-SCHEMA"}


def test_md13_fetch_minus_delay_needs_delayed():
    d = docs()["JPM.json"]; d["last_price"]["as_of_basis"] = "fetch_minus_delay"
    assert check(d, "JPM.json") == set()                                            # delayed true, 15
    d["last_price"].update(delayed=False); del d["last_price"]["delay_minutes"]
    assert check(d, "JPM.json") == {"MD-13-DELAY"}


def test_md13_last_trade_fallback_and_published_accepted():
    d = docs()["JPM.json"]
    d["last_price"]["as_of_basis"] = "last_trade"
    d["iv"]["as_of_basis"] = "published"
    assert check(d, "JPM.json") == set()


def test_1_1_7_document_with_new_fields_is_schema_error():
    d = b117.valid_docs()["JPM.json"]
    assert check(d, "JPM.json") == set()
    d["last_price"]["as_of_basis"] = "quote_time"
    assert check(d, "JPM.json") == {"MD-03-SCHEMA"}
    i = b117.valid_docs()["index.json"]; i["sources_not_used"] = []
    assert check(i, "index.json") == {"MD-03-SCHEMA"}
    i = b117.valid_docs()["index.json"]; i["sources_terms"][0]["basis"] = "robots_only_no_terms"
    del i["sources_terms"][0]["terms_url"]
    assert check(i, "index.json") == {"MD-03-SCHEMA"}
    c = docs()["catalysts.json"]; c["schema_version"] = "1.1.7"
    assert check(c, "catalysts.json") == {"MD-03-SCHEMA"}


def test_1_1_8_document_needs_origin_tags():
    d = docs()["JPM.json"]; del d["earnings_date"]["sources"][0]["origin"]
    assert "MD-03-SCHEMA" in check(d, "JPM.json")
    d = docs()["JPM.json"]; d["earnings_date"]["sources"][1]["origin_entity"] = "bad@entity"
    assert "MD-03-SCHEMA" in check(d, "JPM.json")


# ------------------------------------------------------------------ MD-14 declared delay (cross-file)
def _contract_bid(d):
    return d["JPM.json"]["option_liquidity"]["contracts"][0]["bid"]


def test_md14_undeclared_delay(tmp_path):
    d = docs(); b = _contract_bid(d); b["delayed"] = False; del b["delay_minutes"]
    assert check(d["JPM.json"], "JPM.json") == set()                               # a document alone cannot know
    assert pack_ids(tmp_path / "a", d) == {"MD-14-DELAY"}
    d = docs(); _contract_bid(d)["delay_minutes"] = 10                              # less than the declared 15
    assert pack_ids(tmp_path / "b", d) == {"MD-14-DELAY"}
    d = docs(); _contract_bid(d)["delay_minutes"] = 20                              # more is fine
    assert pack_ids(tmp_path / "c", d) == set()
    d = docs(); d["JPM.json"]["last_price"].update(delayed=False); del d["JPM.json"]["last_price"]["delay_minutes"]
    assert pack_ids(tmp_path / "d", d) == {"MD-14-DELAY"}                           # example_quotes is declared too


def test_md14_scope(tmp_path):
    d = docs(); d["index.json"]["sources_terms"][1]["delayed"] = False; del d["index.json"]["sources_terms"][1]["delay_minutes"]
    b = _contract_bid(d); b["delayed"] = False; del b["delay_minutes"]
    assert pack_ids(tmp_path / "a", d) == set()                                     # not declared delayed: not checked
    d = docs(); d["JPM.json"]["iv"] = bmf.unknown()
    assert pack_ids(tmp_path / "b", d) == set()                                     # no value: nothing to check
    d = docs(); pc = d["catalysts.json"]["ticker_context"][0]["prior_close"]; pc["delay_minutes"] = 1
    assert pack_ids(tmp_path / "c", d) == {"MD-14-DELAY"}                           # catalysts.json fields too
    d = docs(); del d["index.json"]["sources_terms"]
    b = _contract_bid(d); b["delayed"] = False; del b["delay_minutes"]
    assert pack_ids(tmp_path / "d", d) == set()                                     # sources_terms optional


# ------------------------------------------------------------------ MD-07 by origin, MD-07-ORIGIN
def test_md07_independence_is_by_origin_not_domain():
    d = docs()["JPM.json"]
    d["earnings_date"]["sources"] = [bmf.ir("JPM"), src("example_calendar", "https://ir.example.com/cal", "issuer", "JPM")]
    assert check(d, "JPM.json") == {"MD-07-UNCONFIRMED"}                            # one issuer, two pages
    d["earnings_date"]["sources"][1] = src("example_calendar", "https://www.example.com/cal", "data_vendor", "Example Calendar Co")
    assert check(d, "JPM.json") == set()                                            # same domain, two origins: OK in 1.1.8
    d["earnings_date"]["sources"][1] = src("example_calendar", "https://calendar.example.org/c", "issuer", "jpm")
    assert check(d, "JPM.json") == {"MD-07-UNCONFIRMED"}                            # entity compared case-insensitively
    d["earnings_date"]["sources"] = [src("example_wire", "https://a.example.org/x", "news", "Example Wire"),
                                     src("example_calendar", "https://b.example.com/y", "news", "Other Wire")]
    assert check(d, "JPM.json") == set()                                            # same origin, distinct entities


def test_md07_earnings_time_follows_the_two_source_rule():
    d = docs()["JPM.json"]
    d["earnings_time"]["sources"] = [bmf.ir("JPM")]
    assert check(d, "JPM.json") == {"MD-07-UNCONFIRMED"}
    d["earnings_time"]["status"] = "UNCONFIRMED"
    assert check(d, "JPM.json") == set()
    d = docs()["JPM.json"]; d["last_price"]["status"] = "UNCONFIRMED"
    assert check(d, "JPM.json") == {"MD-07-STATUS"}


@pytest.mark.parametrize("url", ["https://www.globenewswire.com/news-release/x", "https://www.prnewswire.com/news/x",
                                 "https://www.businesswire.com/news/home/x", "https://jpm.gcs-web.com/news/x",
                                 "https://ir.example.com/x", "https://investor.example.com/x",
                                 "https://investors.example.org/x"])
def test_md07_origin_issuer_channels(url):
    d = docs()["JPM.json"]
    d["earnings_date"]["sources"][1] = src("example_wire", url, "news", "Example Wire")
    got = check(d, "JPM.json")
    assert "MD-07-ORIGIN" in got and "MD-07-UNCONFIRMED" not in got                 # two origins on paper, still wrong
    d["earnings_date"]["sources"][1] = src("example_wire", url, "issuer", "JPM")
    assert check(d, "JPM.json") == {"MD-07-UNCONFIRMED"}                            # tagged right: one source


def test_md07_origin_issuer_entity_is_the_ticker():
    d = docs()["JPM.json"]; d["earnings_date"]["sources"][0]["origin_entity"] = "WDC"
    assert check(d, "JPM.json") == {"MD-07-ORIGIN"}
    d = docs()["JPM.json"]
    d["earnings_date"]["sources"][1] = src("example_calendar", "https://www.example.org/c", "news", "Example Wire")
    assert check(d, "JPM.json") == set()                                            # not a channel host


def test_md04_on_source_as_of():
    d = docs()["JPM.json"]; d["earnings_date"]["sources"][0]["as_of"] = "2026-10-20T09:00:01-04:00"
    assert check(d, "JPM.json") == {"MD-04-FUTURE"}
    d = docs()["JPM.json"]; d["earnings_date"]["sources"][0]["as_of"] = "2026-10-20T08:00:00"
    assert check(d, "JPM.json") == {"MD-04-TIME-NAIVE"}
    d = docs()["JPM.json"]; d["earnings_date"]["sources"][0]["as_of"] = "2026-13-40T08:00:00-04:00"
    assert "MD-04-TIME-INVALID" in check(d, "JPM.json")


# ------------------------------------------------------------------ MD-15 catalysts.json
def cat():
    return docs()["catalysts.json"]


def test_md15_valid_and_each_suffix():
    assert check(cat(), "catalysts.json") == set()
    c = cat(); c["catalysts"][1]["event_date"] = None
    assert check(c, "catalysts.json") == {"MD-15-UNKNOWN"}
    c = cat(); c["catalysts"][3]["event_date"] = "2026-11-02"; c["catalysts"][3]["sources"] = [bmf.WIRE]
    assert check(c, "catalysts.json") == {"MD-15-VALUE"}
    c["catalysts"][3]["sources"] = []
    assert check(c, "catalysts.json") == {"MD-15-VALUE", "MD-15-UNSOURCED"}
    c = cat(); c["catalysts"][1]["sources"] = []
    assert check(c, "catalysts.json") == {"MD-15-UNSOURCED"}
    c = cat(); c["catalysts"][2]["status"] = "CONFIRMED"
    assert check(c, "catalysts.json") == {"MD-15-UNCONFIRMED"}
    c = cat(); c["catalysts"][2]["event_time"] = "2026-10-28T14:00:00"
    assert check(c, "catalysts.json") == {"MD-15-TIME-NAIVE"}
    c = cat(); c["catalysts"][2]["event_time"] = "2026-10-28T25:00:00-04:00"
    assert check(c, "catalysts.json") == {"MD-15-TIME-INVALID"}
    c = cat(); c["catalysts"][2]["event_time"] = "2026-10-29T03:00:00+00:00"        # 23:00 NY on 10-28: same date
    assert check(c, "catalysts.json") == set()
    c["catalysts"][2]["event_time"] = "2026-10-29T05:00:00+00:00"                   # 01:00 NY on 10-29
    assert check(c, "catalysts.json") == {"MD-15-DATE"}
    c = cat(); c["catalysts"][1]["event_date"] = "2026-02-30"
    assert check(c, "catalysts.json") == {"MD-15-DATE"}
    c = cat(); c["catalysts"][3]["event_time"] = "2026-10-28T14:00:00-04:00"         # time without a date
    assert check(c, "catalysts.json") == {"MD-15-DATE"}
    c = cat(); c["ticker_context"].append(copy.deepcopy(c["ticker_context"][0]))
    assert check(c, "catalysts.json") == {"MD-15-DUPLICATE"}


def test_md15_event_time_optional_and_null():
    c = cat(); c["catalysts"][2]["event_time"] = None
    assert check(c, "catalysts.json") == set()
    del c["catalysts"][2]["event_time"]
    assert check(c, "catalysts.json") == set()


def test_md15_schema_shape():
    c = cat(); c["catalysts"][0]["catalyst_type"] = "rumour"
    assert check(c, "catalysts.json") == {"MD-03-SCHEMA"}
    c = cat(); c["catalysts"][0]["status"] = "OK"
    assert check(c, "catalysts.json") == {"MD-03-SCHEMA"}
    c = cat(); c["catalysts"][0]["account"] = "EXAMPLE"
    assert check(c, "catalysts.json") == {"MD-03-SCHEMA"}
    c = cat(); c["kind"] = "pack_index"
    assert "MD-03-SCHEMA" in check(c, "catalysts.json")
    c = cat(); c["ticker_context"][1]["analyst_actions"][0]["to"] = "x@y"
    assert "MD-03-SCHEMA" in check(c, "catalysts.json")
    c = cat(); c["ticker_context"][1]["analyst_actions"][0]["action"] = "love"
    assert check(c, "catalysts.json") == {"MD-03-SCHEMA"}
    c = cat(); c["catalysts"][0]["ticker"] = "jpm"
    assert check(c, "catalysts.json") == {"MD-03-SCHEMA"}


def test_md15_nested_rules():
    c = cat(); c["catalysts"][0]["sources"][0]["origin_entity"] = "WDC"
    assert check(c, "catalysts.json") == {"MD-07-ORIGIN"}                           # entry ticker JPM, issuer says WDC
    c = cat(); c["catalysts"][2]["sources"] = [src("example_calendar", "https://www.globenewswire.com/x", "issuer", "Federal Reserve")]
    assert check(c, "catalysts.json") == set()                                      # macro: issuer entity is free text
    c = cat(); c["catalysts"][0]["sources"][1]["as_of"] = "2026-10-21T00:00:00-04:00"
    assert check(c, "catalysts.json") == {"MD-04-FUTURE"}
    c = cat(); c["ticker_context"][1]["analyst_actions"][0]["date"] = "2026-10-21"
    assert check(c, "catalysts.json") == {"MD-04-FUTURE"}
    c["ticker_context"][1]["analyst_actions"][0]["date"] = "2026-10-20"
    assert check(c, "catalysts.json") == set()                                      # the pack's own NY date is fine
    c["ticker_context"][1]["analyst_actions"][0]["date"] = "2026-02-30"
    assert check(c, "catalysts.json") == {"MD-15-DATE"}
    c = cat(); c["ticker_context"][1]["analyst_actions"][0]["source"]["url"] = "https://ir.example.com/x"
    assert check(c, "catalysts.json") == {"MD-07-ORIGIN"}
    c = cat(); c["catalysts"][0]["note"] = "EXAMPLE contact ops@example.com"
    assert check(c, "catalysts.json") == {"MD-09-EMAIL"}


def test_md15_prior_close_is_a_sourced_field_never_stale():
    c = cat(); pc = c["ticker_context"][0]["prior_close"]
    assert pc["status"] == "OK" and pc["as_of"] == bmf.PRIOR_CLOSE_AS_OF            # 17 h old, still OK
    assert check(c, "catalysts.json") == set()
    del pc["as_of_basis"]
    assert check(c, "catalysts.json") == {"MD-13-MISSING"}
    c = cat(); c["ticker_context"][0]["prior_close"]["value"] = None
    assert check(c, "catalysts.json") == {"MD-06-UNKNOWN"}
    c = cat(); c["ticker_context"][0]["prior_close"]["as_of"] = "2026-10-20T09:30:00-04:00"
    assert check(c, "catalysts.json") == {"MD-04-FUTURE"}
    c = cat(); c["ticker_context"][0]["prior_close"]["status"] = "UNCONFIRMED"
    assert check(c, "catalysts.json") == {"MD-07-STATUS"}
    c = cat(); c["ticker_context"][0]["prior_close"]["delay_minutes"] = 0
    assert "MD-03-SCHEMA" in check(c, "catalysts.json")


# ------------------------------------------------------------------ catalysts.json in the pack (loader)
def test_catalysts_header_and_sources_vs_index(tmp_path):
    for i, (k, v) in enumerate([("pack_id", "EXAMPLE-other"), ("pack_built_at", "2026-10-20T08:59:00-04:00"),
                                ("data_label", "TEST"), ("date", "2026-10-19")]):
        d = docs(); d["catalysts.json"][k] = v
        assert pack_ids(tmp_path / str(i), d) == {"MD-11-PACK"}, k
    d = docs(); d["index.json"]["sources_terms"] = [s for s in d["index.json"]["sources_terms"]
                                                     if s["source"] != "example_screener"]
    assert pack_ids(tmp_path / "s", d) == {"MD-12-SOURCE"}                          # used only in catalysts.json


def test_catalysts_optional_and_ticker_free(tmp_path):
    d = docs(); del d["catalysts.json"]
    r = mdl.load_pack(write_pack(tmp_path / "a", d))
    assert r.ok and r.catalysts is None
    d = docs(); d["catalysts.json"]["catalysts"][0]["ticker"] = "ZZZZ"              # not in the index: allowed
    d["catalysts.json"]["catalysts"][0]["sources"] = [bmf.ir("ZZZZ"), bmf.CALENDAR]
    assert pack_ids(tmp_path / "b", d) == set()


def test_catalysts_file_must_be_listed_and_named(tmp_path):
    files = bmf.with_manifest(bmf.pack({k: v for k, v in docs().items() if k != "catalysts.json"}))
    files["catalysts.json"] = bmf.dump(cat())
    p = tmp_path / "a" / bmf.DATE; p.mkdir(parents=True)
    for n, b in files.items():
        (p / n).write_bytes(b)
    assert ids(mdl.load_pack(p).errors) == {"MD-02-EXTRA"}
    assert pack_ids(tmp_path / "b", docs(), extra={"catalyst.json": bmf.dump(cat())}) == {"MD-01-NAME", "MD-02-EXTRA"}


def test_catalysts_wrong_kind_or_bytes(tmp_path):
    d = docs(); d["catalysts.json"] = d["index.json"]
    assert "MD-03-SCHEMA" in pack_ids(tmp_path / "a", d)
    files = bmf.pack(docs()); files["catalysts.json"] = b"{not json\n"
    p = tmp_path / "b" / bmf.DATE; p.mkdir(parents=True)
    for n, b in bmf.with_manifest(files).items():
        (p / n).write_bytes(b)
    r = mdl.load_pack(p)
    assert ids(r.errors) == {"MD-03-JSON"} and r.exit_code() == 2 and r.catalysts is None


def test_catalysts_not_allowed_in_a_1_1_7_pack(tmp_path):
    c = cat(); c["schema_version"] = "1.1.7"
    p = write_pack(tmp_path / "a", b117.valid_docs(), extra={"catalysts.json": bmf.dump(c)}, builder=b117)
    r = mdl.load_pack(p)
    assert ids(r.errors) == {"MD-01-NAME", "MD-02-EXTRA"} and not r.ok and r.catalysts is None
    assert mdl.load_pack(write_pack(tmp_path / "b", b117.valid_docs(), builder=b117)).ok


def test_catalysts_toctou_recheck(tmp_path, monkeypatch):
    """the peek only widens the name whitelist; the verified (hashed, parsed) index must agree"""
    p = write_pack(tmp_path, b117.valid_docs(), extra={"catalysts.json": bmf.dump(cat())}, builder=b117)
    monkeypatch.setattr(mdl, "_peek_v118", lambda dfd, names: True)              # as if index.json said 1.1.8 at peek
    r = mdl.load_pack(p)
    assert "MD-01-NAME" in ids(r.errors) and not r.ok and r.catalysts is None
    assert any("verified index.json" in e for e in r.errors)


def test_mixed_versions_in_one_pack(tmp_path):
    d = docs(); d["JPM.json"] = b117.valid_docs()["JPM.json"]
    assert pack_ids(tmp_path / "a", d) == {"MD-11-PACK", "MD-14-DELAY"}            # the 1.1.7 chain said delayed false
    d = b117.valid_docs(); d["JPM.json"] = docs()["JPM.json"]
    assert "MD-11-PACK" in pack_ids(tmp_path / "b", d, builder=b117)


def test_peek_ignores_a_symlinked_index(tmp_path):
    p = write_pack(tmp_path / "a", b117.valid_docs(), extra={"catalysts.json": bmf.dump(cat())}, builder=b117)
    real = tmp_path / "real_index.json"
    real.write_bytes(bmf.dump(docs()["index.json"]))
    (p / "index.json").unlink()
    (p / "index.json").symlink_to(real)
    r = mdl.load_pack(p)
    assert "MD-01-SYMLINK" in ids(r.errors) and not r.ok and r.catalysts is None


# ------------------------------------------------------------------ docs
def test_docs_cover_the_1_1_8_notes():
    md = (SPEC / "market_data_rules.md").read_text()
    assert md.splitlines()[0].startswith("# Market data rules v1.1.8 ")
    assert "MD-05 is not R01" in md and "15 minutes" in md and "20 minutes" in md
    assert "conservative fallback" in md and "summary.md` only" in md
    assert "robots_only_no_terms" in md and "sources_not_used" in md and "catalysts.json" in md
    y = (SPEC / "market_data_rules.yaml").read_text()
    assert "R01" in y and "summary" in y
