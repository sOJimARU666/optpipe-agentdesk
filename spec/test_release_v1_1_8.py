"""Spec 1.1.8: schema_version 1.1.8 for session records (same shape and checks as 1.1.7) and market_snapshot packs
(new optional pack fields, MD-13..MD-15; tested in test_market_snapshot_v1_1_8.py). Back-compat is proved against the
frozen 1.1.7 release in _v1_1_7_backup/ (subprocess, its own modules): 1.1.7 session records and 1.1.7 market packs
validate exactly as before (same error strings). PAPER ONLY."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

import build_market_data_fixtures as bmf
import build_market_data_fixtures_v1_1_7 as b117
import market_data_loader as mdl
import risk_reference as rr
import session_fixtures as fx
import test_release_v1_1_4 as r114          # module imports only: their tests are not collected twice
import test_release_v1_1_5 as r115
import test_release_v1_1_6 as r116
import test_release_v1_1_7 as r117
import test_watcher_reference as twr
import validate_market_snapshot as vms
import validate_session as vs
import watcher_reference as wr

SPEC = Path(__file__).resolve().parent
BACKUP_117 = SPEC / "_v1_1_7_backup"
MANIFEST_117_SHA256 = "91f7fd260d214708c0b50a0fa307e0f4203492c86fcffd5ff5e3c705811cd1c4"
PY = sys.executable
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
NOW = twr.SESSION_NOW
OLD_RECORDS = r117.OLD_RECORDS + [BACKUP_117 / "session.example.json"]
OLD_VERSIONS = r117.OLD_VERSIONS + ["1.1.7"]
FIX_117 = SPEC / "fixtures" / "market_data_v1_1_7"

_MARKET = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import market_data_loader as mdl
import validate_market_snapshot as vms
p = json.load(sys.stdin)
out = {"docs": [vms.validate_document(d, n) for d, n in p["docs"]], "packs": []}
for d in p["packs"]:
    r = mdl.load_pack(d)
    out["packs"].append([r.errors, r.exit_code(), r.ok, sorted(r.snapshots)])
print(json.dumps(out))
"""


def _frozen(script, payload):
    out = subprocess.run([PY, "-B", "-c", script, str(BACKUP_117)], input=json.dumps(payload), capture_output=True,
                         text=True, env=ENV, cwd=str(BACKUP_117), check=True)
    return json.loads(out.stdout)


def _current_market(payload):
    out = {"docs": [vms.validate_document(d, n) for d, n in payload["docs"]], "packs": []}
    for d in payload["packs"]:
        r = mdl.load_pack(d)
        out["packs"].append([r.errors, r.exit_code(), r.ok, sorted(r.snapshots)])
    return json.loads(json.dumps(out))


# ------------------------------------------------------------------ labels
def test_schema_version_1_1_8_listed_and_labels_track_version():
    enum = json.loads((SPEC / "session.schema.json").read_text())["properties"]["schema_version"]["enum"]
    assert enum == OLD_VERSIONS + ["1.1.8"]
    assert fx.example()["schema_version"] == "1.1.8" and vs.validate(fx.example()) == []
    v = (SPEC / "VERSION").read_text().strip()
    assert v == "1.1.8"
    assert rr.load_rules()["ruleset_version"] == v and wr.load_watcher_rules()["watcher_version"] == v
    assert vms.RULES["market_data_rules_version"] == v and vms.RULES["schema_versions"] == ["1.1.7", v]
    for md, head in (("risk_rules.md", "# Risk rules v"), ("watcher_rules.md", "# Watcher rules v"),
                     ("market_data_rules.md", "# Market data rules v")):
        assert (SPEC / md).read_text().splitlines()[0].startswith(f"{head}{v} "), md
    assert "## 1.1.8 (2026-11-03)" in (SPEC / "CHANGELOG.md").read_text()


def test_example_regenerated_only_labels_changed():
    """build_example.py output = the frozen 1.1.7 example with the version labels bumped"""
    old = json.loads((BACKUP_117 / "session.example.json").read_text())
    assert json.loads(json.dumps(old).replace('"1.1.7"', '"1.1.8"')) == fx.example()
    assert json.dumps(old).count('"1.1.7"') == 4


# ------------------------------------------------------------------ frozen 1.1.7 release
def test_frozen_1_1_7_backup_is_the_1_1_7_release():
    assert (BACKUP_117 / "VERSION").read_text().strip() == "1.1.7"
    assert json.loads((BACKUP_117 / "session.example.json").read_text())["schema_version"] == "1.1.7"
    man = (BACKUP_117 / "MANIFEST.sha256").read_bytes()
    assert hashlib.sha256(man).hexdigest() == MANIFEST_117_SHA256
    lines = man.decode().splitlines()
    assert len(lines) == 90
    for line in lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((BACKUP_117 / name).read_bytes()).hexdigest() == digest, name


def test_backup_is_not_collected_and_not_in_manifest():
    assert "_v1_1_7_backup" in __import__("conftest").collect_ignore
    assert "_v1_1_7_backup" not in (SPEC / "MANIFEST.sha256").read_text()


# ------------------------------------------------------------------ session records: 1.1.8 = 1.1.7 checks
def test_backcompat_old_records_identical_to_frozen_1_1_7():
    recs = [json.loads(p.read_text()) for p in OLD_RECORDS]   # read_text raises (FAIL) if a record is missing
    assert len(recs) == 12
    new = [vs.validate(r) for r in recs]
    assert new == _frozen(r115._VALIDATE, recs) and all(e == [] for e in new)


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_backcompat_mutations_identical_to_frozen_1_1_7(version):
    recs = r116._mutated_1_1_6(version)
    assert [vs.validate(r) for r in recs] == _frozen(r115._VALIDATE, recs)


def test_1_1_8_records_get_exactly_the_1_1_7_checks():
    old, new = r116._mutated_1_1_6("1.1.7"), r116._mutated_1_1_6("1.1.8")
    frozen = _frozen(r115._VALIDATE, old)
    for i, (o, n) in enumerate(zip(old, new)):
        assert vs.validate(n) == vs.validate(o) == frozen[i], i


def test_frozen_1_1_7_rejects_1_1_8_label():
    assert any(e.startswith("schema schema_version") for e in _frozen(r115._VALIDATE, [fx.example()])[0])


def test_watcher_and_risk_yaml_only_labels_changed():
    for name, key in (("watcher_rules.yaml", "watcher_version"), ("risk_rules.yaml", "ruleset_version")):
        old = yaml.safe_load((BACKUP_117 / name).read_text())
        cur = yaml.safe_load((SPEC / name).read_text())
        assert set(old) == set(cur) and {k for k in cur if cur[k] != old[k]} == {key}, name
    assert yaml.safe_load((BACKUP_117 / "watcher_rules.yaml").read_text())["tests"] == twr.CASES


def test_session_cases_and_grid_identical_to_frozen_1_1_7_reference():
    cases = [c for c in twr.CASES if any(k in c for k in twr.SESSION_KEYS)]
    recs = [r115._case_record(c) for c in cases] + [s for _, s in r115._grid()]
    old = _frozen(r115._TARGET, [[s, fx.TK, NOW] for s in recs])
    new = [wr.target_from_session(s, fx.TK, now=NOW) for s in recs]
    assert len(recs) == 14 + 270 and old == new


def test_risk_yaml_cases_identical_to_frozen_1_1_7():
    cur = rr.load_rules()
    old = _frozen(r116._EVAL, None)
    new = [[c["id"], o["results"], o["overall"]] for c in cur["tests"]
           for o in [rr.evaluate(rr.apply_case(cur["fixture"], c), cur, c.get("config"), profile=c.get("profile"))]]
    assert json.loads(json.dumps(new, default=str)) == old


# ------------------------------------------------------------------ market packs: 1.1.7 = frozen 1.1.7
def test_v1_1_7_builder_output_is_the_frozen_1_1_7_fixtures():
    built = {f"{case}/{rel}": b for case, files in b117.build().items() for rel, b in files.items()}
    frozen = BACKUP_117 / "fixtures" / "market_data"
    old = {p.relative_to(frozen).as_posix(): p.read_bytes() for p in frozen.rglob("*") if p.is_file()}
    vendored = {p.relative_to(FIX_117).as_posix(): p.read_bytes() for p in FIX_117.rglob("*") if p.is_file()}
    assert built == old == vendored and len(old) == 51
    assert b117.EXPECTED == {k: (rc, set(w)) for k, (rc, w) in b117.EXPECTED.items()}


def _doc_mutations():
    """1.1.7 documents with every field broken in several ways (+ 1.1.8-only fields and labels): [doc, name] pairs"""
    out = []
    base = b117.valid_docs()
    for name, d in base.items():
        out.append([d, name])
        if d["kind"] == "ticker_snapshot":
            for path, leaf, f in vms.iter_fields(d):
                for mut in ({"value": None}, {"status": "UNKNOWN"}, {"status": "UNCONFIRMED"}, {"status": "STALE"},
                            {"delayed": True}, {"delayed": False, "delay_minutes": 3}, {"source": None},
                            {"as_of": "2026-10-20T09:00:01-04:00"}, {"as_of": "2026-10-20T08:00:00"},
                            {"as_of": "2026-10-20T08:39:59-04:00"}, {"as_of_basis": "quote_time"},
                            {"sources": [{"source": "example_ir", "url": "https://www.globenewswire.com/x",
                                          "as_of": "2026-10-20T08:00:00-04:00"},
                                         {"source": "example_wire", "url": "https://ir.example.org/x",
                                          "as_of": "2026-10-20T08:00:00-04:00"}]}):
                    m = copy.deepcopy(d)
                    tgt = m
                    for k in path.replace("]", "").replace("[", ".").split("."):
                        tgt = tgt[int(k)] if k.isdigit() else tgt[k]
                    tgt.update(mut)
                    out.append([m, name])
            for k, v in (("schema_version", "1.1.8"), ("watchlist", "street"), ("candidate_status", "candidate"),
                         ("pack_built_at", "2026-10-20T09:00:00"), ("note", "ops@example.com")):
                m = copy.deepcopy(d); m[k] = v; out.append([m, name])
        else:
            for mut in (lambda i: i.update(schema_version="1.1.8"), lambda i: i.update(sources_not_used=[]),
                        lambda i: i["sources_terms"][0].update(basis="robots_only_no_terms"),
                        lambda i: i["sources_terms"][0].update(delayed=True, delay_minutes=15),
                        lambda i: i["tickers"][3].update(candidate_status="candidate"),
                        lambda i: i["sources_terms"][0].update(note="ops@example.com")):
                m = copy.deepcopy(d); mut(m); out.append([m, name])
    for name, d in bmf.valid_docs().items():                          # 1.1.8 docs: the frozen validator rejects them
        out.append([d, name])
    return out


def test_market_documents_identical_to_frozen_1_1_7():
    docs = _doc_mutations()
    payload = {"docs": docs, "packs": []}
    old, new = _frozen(_MARKET, payload), _current_market(payload)
    is118 = [d.get("schema_version") == "1.1.8" for d, _ in docs]
    assert len(docs) > 500 and sum(is118) > len(bmf.valid_docs())
    for i, (o, n) in enumerate(zip(old["docs"], new["docs"])):
        if is118[i]:                                                   # frozen 1.1.7 rejects every 1.1.8 document
            assert o and any(x.startswith("MD-03-SCHEMA") for x in o), i
        else:                                                          # every 1.1.7 document: identical errors
            assert n == o, (i, docs[i][1])


def _write(root: Path, files: dict[str, bytes]) -> str:
    for rel, b in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
        p.chmod(0o644)
    return str(root / b117.DATE)


def _pack_mutations(tmp_path: Path) -> list[str]:
    dirs = [str(FIX_117 / c / b117.DATE) for c in sorted(b117.EXPECTED)]
    dirs += [str(BACKUP_117 / "fixtures" / "market_data" / c / b117.DATE) for c in sorted(b117.EXPECTED)]
    n = 0

    def add(docs, extra=None, summary=True, manifest=True):
        nonlocal n
        files = b117.pack(docs, summary=summary)
        files.update(extra or {})
        if manifest:
            files = b117.with_manifest(files)
        n += 1
        dirs.append(_write(tmp_path / f"p{n:03d}", {f"{b117.DATE}/{k}": v for k, v in files.items()}))

    d = b117.valid_docs(); add(d)
    d = b117.valid_docs(); add(d, summary=False)
    d = b117.valid_docs(); add(d, manifest=False)
    cat = bmf.catalysts_doc(); add(b117.valid_docs(), extra={"catalysts.json": bmf.dump(cat)})
    cat["schema_version"] = "1.1.7"; add(b117.valid_docs(), extra={"catalysts.json": bmf.dump(cat)})
    d = b117.valid_docs(); d["index.json"]["sources_terms"].pop(3); add(d)
    d = b117.valid_docs(); d["JPM.json"]["pack_id"] = "EXAMPLE-other"; add(d)
    d = b117.valid_docs(); d["index.json"]["date"] = "2026-10-19"; add(d)
    d = b117.valid_docs(); d["index.json"]["tickers"].append(dict(d["index.json"]["tickers"][0])); add(d)
    d = b117.valid_docs(); del d["WDC.json"]; add(d)
    d = b117.valid_docs(); d["JPM.json"]["option_liquidity"]["contracts"][0]["bid"]["delayed"] = False; add(d)
    d = b117.valid_docs(); d["JPM.json"]["earnings_date"]["sources"][1]["url"] = "https://www.globenewswire.com/x"; add(d)
    add(b117.valid_docs(), extra={"notes.md": b"# EXAMPLE\n"})
    add(b117.valid_docs(), extra={"refresh.py": b"x\n"})
    add(bmf.valid_docs())                                              # a 1.1.8 pack: the frozen loader rejects it
    return dirs


def _mixed_packs(tmp_path: Path) -> list[str]:
    """packs mixing 1.1.7 and 1.1.8 labels: both releases reject them (with their own ids)"""
    out = []
    for i, mut in enumerate((lambda d: d["JPM.json"].update(schema_version="1.1.8"),
                             lambda d: d["index.json"].update(schema_version="1.1.8"),
                             lambda d: d.update({"JPM.json": bmf.valid_docs()["JPM.json"]}))):
        d = b117.valid_docs(); mut(d)
        files = b117.with_manifest(b117.pack(d))
        out.append(_write(tmp_path / f"m{i}", {f"{b117.DATE}/{k}": v for k, v in files.items()}))
    return out


def test_market_packs_identical_to_frozen_1_1_7(tmp_path):
    dirs = _pack_mutations(tmp_path)
    payload = {"docs": [], "packs": dirs}
    old, new = _frozen(_MARKET, payload), _current_market(payload)
    assert len(dirs) == 2 * len(b117.EXPECTED) + 15
    assert new["packs"][:-1] == old["packs"][:-1]
    errs, rc, ok, _ = old["packs"][-1]
    assert not ok and rc != 0                                          # frozen 1.1.7 rejects a 1.1.8 pack
    assert new["packs"][-1][2] is True                                 # spec 1.1.8 accepts it
    for i, c in enumerate(sorted(b117.EXPECTED)):                      # and the frozen fixtures give EXPECTED
        rc, want = b117.EXPECTED[c]
        assert old["packs"][i][1] == rc and want <= {e.split(" ", 1)[0] for e in old["packs"][i][0]}


def test_frozen_1_1_7_cli_on_1_1_7_fixtures_matches_current():
    for c in sorted(b117.EXPECTED):
        d = BACKUP_117 / "fixtures" / "market_data" / c / b117.DATE
        old = subprocess.run([PY, "-B", str(BACKUP_117 / "validate_market_snapshot.py"), str(d)], capture_output=True,
                             text=True, env=ENV, cwd=str(BACKUP_117))
        new = subprocess.run([PY, "-B", str(SPEC / "validate_market_snapshot.py"), str(d)], capture_output=True,
                             text=True, env=ENV, cwd=str(SPEC))
        assert old.returncode == new.returncode, c
        if old.returncode != 0:
            assert old.stdout == new.stdout and old.stderr == new.stderr, c


def test_mixed_version_packs_rejected_by_both(tmp_path):
    dirs = _mixed_packs(tmp_path)
    payload = {"docs": [], "packs": dirs}
    for res in (_frozen(_MARKET, payload), _current_market(payload)):
        assert [r[2] for r in res["packs"]] == [False] * 3 and all(r[1] != 0 for r in res["packs"])


# ------------------------------------------------------------------ docs
def test_docs_mention_1_1_8():
    log = (SPEC / "CHANGELOG.md").read_text()
    for s in ("robots_only_no_terms", "sources_not_used", "as_of_basis", "MD-13", "MD-14", "MD-15", "catalysts.json",
              "origin_entity", "MD-07-ORIGIN", "MD-12-CONFLICT"):
        assert s in log, s
    readme = (SPEC / "fixtures" / "README.md").read_text()
    assert "market_data_v1_1_7" in readme and "catalysts_bad" in readme


# ------------------------------------------------------------------ hygiene of the new / changed files
NEW_FILES = ("test_release_v1_1_8.py", "test_market_snapshot_v1_1_8.py", "test_market_snapshot_v1_1_7.py",
             "test_release_v1_1_7.py", "validate_market_snapshot.py", "market_data_loader.py",
             "build_market_data_fixtures.py", "build_market_data_fixtures_v1_1_7.py", "market_snapshot.schema.json",
             "market_data_rules.yaml", "market_data_rules.md", "watcher_rules.yaml", "watcher_rules.md",
             "risk_rules.yaml", "risk_rules.md", "validate_session.py", "session.schema.json", "CHANGELOG.md",
             "fixtures/README.md")


def test_new_files_hygiene():
    files = [SPEC / n for n in NEW_FILES] + sorted(p for p in (SPEC / "fixtures" / "market_data").rglob("*") if p.is_file())
    assert len(files) > len(NEW_FILES)
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert not r114.ABS_PATH.findall(text), p.name
        assert all(r114.EMAIL_OK.search(m) for m in r114.EMAIL.findall(text)), p.name


def test_pack_fixtures_hold_no_email_at_all():
    for p in (SPEC / "fixtures" / "market_data").rglob("*"):
        if p.is_file() and "outside" not in p.name:
            assert not vms.EMAIL.search(p.read_text(encoding="utf-8", errors="replace")), p
