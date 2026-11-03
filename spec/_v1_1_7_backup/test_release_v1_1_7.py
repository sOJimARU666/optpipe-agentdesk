"""Spec 1.1.7: schema_version 1.1.7 (same shape and checks as 1.1.6), the market_snapshot pack files (tested in
test_market_snapshot_v1_1_7.py) and the removal of the duplicate top-level watcher `target_precedence` (Moodle).
Back-compat is proved against the frozen 1.1.6 release in _v1_1_6_backup/ (subprocess, its own modules). PAPER ONLY."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import risk_reference as rr
import session_fixtures as fx
import test_release_v1_1_4 as r114          # module imports only: their tests are not collected twice
import test_release_v1_1_5 as r115
import test_release_v1_1_6 as r116
import test_watcher_reference as twr
import validate_market_snapshot as vms
import validate_session as vs
import watcher_reference as wr

SPEC = Path(__file__).resolve().parent
BACKUP_116 = SPEC / "_v1_1_6_backup"
MANIFEST_116_SHA256 = "6b4c51ce10c71a885d8e009436fedd28d9cf6cb1feaa5a704b63d7241099a647"
PY = sys.executable
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
NOW = twr.SESSION_NOW
OLD_RECORDS = r116.OLD_RECORDS + [BACKUP_116 / "session.example.json"]
OLD_VERSIONS = r116.OLD_VERSIONS + ["1.1.6"]


def _frozen(script, payload):
    out = subprocess.run([PY, "-B", "-c", script, str(BACKUP_116)], input=json.dumps(payload), capture_output=True,
                         text=True, env=ENV, cwd=str(BACKUP_116), check=True)
    return json.loads(out.stdout)


# ------------------------------------------------------------------ labels
def test_schema_version_1_1_7_listed_and_labels_track_version():
    enum = json.loads((SPEC / "session.schema.json").read_text())["properties"]["schema_version"]["enum"]
    assert enum == OLD_VERSIONS + ["1.1.7"]
    assert fx.example()["schema_version"] == "1.1.7" and vs.validate(fx.example()) == []
    v = (SPEC / "VERSION").read_text().strip()
    assert v == "1.1.7"
    assert rr.load_rules()["ruleset_version"] == v and wr.load_watcher_rules()["watcher_version"] == v
    assert vms.RULES["market_data_rules_version"] == v and vms.RULES["schema_versions"] == [v]
    assert json.loads((SPEC / "market_snapshot.schema.json").read_text())["$defs"]["TickerSnapshot"]["properties"][
        "schema_version"]["enum"] == [v]
    for md, head in (("risk_rules.md", "# Risk rules v"), ("watcher_rules.md", "# Watcher rules v"),
                     ("market_data_rules.md", "# Market data rules v")):
        assert (SPEC / md).read_text().splitlines()[0].startswith(f"{head}{v} "), md


def test_example_regenerated_only_labels_changed():
    """build_example.py output = the frozen 1.1.6 example with the three version labels bumped"""
    old = json.loads((BACKUP_116 / "session.example.json").read_text())
    assert json.loads(json.dumps(old).replace('"1.1.6"', '"1.1.7"')) == fx.example()
    assert json.dumps(old).count('"1.1.6"') == 4


# ------------------------------------------------------------------ frozen 1.1.6 release
def test_frozen_1_1_6_backup_is_the_1_1_6_release():
    assert (BACKUP_116 / "VERSION").read_text().strip() == "1.1.6"
    assert json.loads((BACKUP_116 / "session.example.json").read_text())["schema_version"] == "1.1.6"
    man = (BACKUP_116 / "MANIFEST.sha256").read_bytes()
    assert hashlib.sha256(man).hexdigest() == MANIFEST_116_SHA256
    lines = man.decode().splitlines()
    assert len(lines) == 31
    for line in lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((BACKUP_116 / name).read_bytes()).hexdigest() == digest, name


def test_backup_is_not_collected_and_not_in_manifest():
    assert "_v1_1_6_backup" in __import__("conftest").collect_ignore
    assert "_v1_1_6_backup" not in (SPEC / "MANIFEST.sha256").read_text()


def test_backcompat_old_records_identical_to_frozen_1_1_6():
    recs = [json.loads(p.read_text()) for p in OLD_RECORDS]   # read_text raises (FAIL) if a record is missing
    assert len(recs) == 11
    new = [vs.validate(r) for r in recs]
    assert new == _frozen(r115._VALIDATE, recs) and all(e == [] for e in new)


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_backcompat_mutations_identical_to_frozen_1_1_6(version):
    """every 1.1.3 rule broken + tamper kinds + the 1.1.6 G4/G5/G6 cases, labelled with every old version: the current
    validator returns exactly the frozen 1.1.6 validator's error strings"""
    recs = r116._mutated_1_1_6(version)
    assert [vs.validate(r) for r in recs] == _frozen(r115._VALIDATE, recs)


def test_1_1_7_records_get_exactly_the_1_1_6_checks():
    """a 1.1.7 record is checked exactly like a 1.1.6 record: same error strings for every mutation, and the frozen
    1.1.6 validator gives those strings for the 1.1.6 label"""
    old, new = r116._mutated_1_1_6("1.1.6"), r116._mutated_1_1_6("1.1.7")
    frozen = _frozen(r115._VALIDATE, old)
    for i, (o, n) in enumerate(zip(old, new)):
        assert vs.validate(n) == vs.validate(o) == frozen[i], i


def test_frozen_1_1_6_rejects_1_1_7_label():
    """1.1.7 is new: the frozen 1.1.6 validator flags it as an unknown schema_version (consumers upgrade together)"""
    assert any(e.startswith("schema schema_version") for e in _frozen(r115._VALIDATE, [fx.example()])[0])


# ------------------------------------------------------------------ W / R cases: nothing changed
def test_watcher_yaml_only_the_duplicate_precedence_removed():
    old = yaml.safe_load((BACKUP_116 / "watcher_rules.yaml").read_text())
    assert old["tests"] == twr.CASES and len(twr.CASES) == 59
    assert set(old) - set(twr.WR) == {"target_precedence"} and set(twr.WR) <= set(old)
    for k in twr.WR:
        if k != "watcher_version":
            assert old[k] == twr.WR[k], k


def test_session_cases_and_grid_identical_to_frozen_1_1_6_reference():
    cases = [c for c in twr.CASES if any(k in c for k in twr.SESSION_KEYS)]
    recs = [r115._case_record(c) for c in cases] + [s for _, s in r115._grid()]
    old = _frozen(r115._TARGET, [[s, fx.TK, NOW] for s in recs])
    new = [wr.target_from_session(s, fx.TK, now=NOW) for s in recs]
    assert len(recs) == 14 + 270 and old == new


def test_watcher_yaml_cases_identical_to_frozen_1_1_6():
    for c in twr.CASES:
        twr.test_watcher_case(c)


def test_risk_yaml_cases_unchanged_vs_frozen_1_1_6():
    old_rules = yaml.safe_load((BACKUP_116 / "risk_rules.yaml").read_text())
    cur = rr.load_rules()
    assert old_rules["tests"] == cur["tests"] and len(cur["tests"]) == 78
    assert {k for k in cur if cur[k] != old_rules.get(k)} == {"ruleset_version"}
    old = _frozen(r116._EVAL, None)
    new = [[c["id"], o["results"], o["overall"]] for c in cur["tests"]
           for o in [rr.evaluate(rr.apply_case(cur["fixture"], c), cur, c.get("config"), profile=c.get("profile"))]]
    assert json.loads(json.dumps(new, default=str)) == old


# ------------------------------------------------------------------ target_precedence: scope is the only source
def test_top_level_target_precedence_removed_scope_is_the_only_source():
    assert "target_precedence" not in twr.WR
    assert twr.WR["scope"]["target_precedence"] == list(wr.TARGET_PRECEDENCE)
    assert twr.WR["real_approval"]["step"] == "5"                     # step 5 definition kept (not a duplicate)
    frozen = yaml.safe_load((BACKUP_116 / "watcher_rules.yaml").read_text())["target_precedence"]
    assert [x["id"] for x in frozen] == twr.WR["scope"]["target_precedence"]   # nothing lost by the removal
    raw = (SPEC / "watcher_rules.yaml").read_text()
    assert sum(1 for l in raw.splitlines() if l.lstrip().startswith("target_precedence:")) == 1


def test_docs_no_longer_point_at_the_top_level_list():
    md = (SPEC / "watcher_rules.md").read_text()
    assert "explicit top-level YAML `target_precedence` list, one entry per step" not in md
    assert "`scope.target_precedence` is the ONLY machine-readable precedence" in md


# ------------------------------------------------------------------ hygiene of the new / changed files
NEW_FILES = ("test_release_v1_1_7.py", "test_market_snapshot_v1_1_7.py", "validate_market_snapshot.py",
             "market_data_loader.py", "build_market_data_fixtures.py", "market_snapshot.schema.json",
             "market_data_rules.yaml", "market_data_rules.md", "watcher_rules.yaml", "watcher_rules.md",
             "validate_session.py", "session.schema.json", "CHANGELOG.md", "fixtures/README.md")


def test_new_files_hygiene():
    files = [SPEC / n for n in NEW_FILES] + sorted(p for p in (SPEC / "fixtures" / "market_data").rglob("*") if p.is_file())
    assert len(files) > len(NEW_FILES)
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert not r114.ABS_PATH.findall(text), p.name
        assert all(r114.EMAIL_OK.search(m) for m in r114.EMAIL.findall(text)), p.name
