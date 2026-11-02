"""Spec 1.1.4: hermetic fixtures (no machine paths, no checkout-name dependency), schema_version 1.1.4 = 1.1.3 checks
(no new invariants), and back-compat with the frozen 1.1.3 validator in _v1_1_3_backup. PAPER ONLY."""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import session_fixtures as fx
import validate_session as vs
import watcher_reference as wr

SPEC = Path(__file__).resolve().parent
FIXTURES = SPEC / "fixtures"
BACKUP_113 = SPEC / "_v1_1_3_backup"
PY = sys.executable
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

FIXTURE_RECORDS = ["session-2026-10-13.json", "session-2026-10-13.v1_1.json", "spec_patches_v1_1.session.example.json"]
FIXTURE_FILES = FIXTURE_RECORDS + ["session-2026-10-13.trace.json", "README.md"]
OLD_RECORDS = [SPEC / "_v1_backup/session.example.json", SPEC / "_v1_1_0_backup/session.example.json",
               SPEC / "_v1_1_1_backup/session.example.json", SPEC / "_v1_1_2_backup/session.example.json",
               BACKUP_113 / "session.example.json"] + [FIXTURES / f for f in FIXTURE_RECORDS]

# same patterns as the pipeline repo's hygiene check (absolute machine paths, non-placeholder emails)
ABS_PATH = re.compile(r"(?<![\w.~-])/(?:workspace|home|tmp|root|cursor|Users|var/folders)/[^\s\"'<]*")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TMP = "/" + "tmp"                      # spelled split so this file passes its own scan
CHECKOUT_NAME = "options" + "-pipeline"   # the consumer repo's checkout dir name, never referenced by spec code
EMAIL_OK = re.compile(r"@(?:example\.(?:com|org|net)|localhost)$|^fred@stlouisfed\.org$")


def release_files():
    """the files the manifest covers (same exclusions as the release find command)"""
    out = []
    for p in sorted(SPEC.rglob("*")):
        rel = p.relative_to(SPEC)
        if not p.is_file() or rel.parts[0] == ".venv" or re.fullmatch(r"_v.*_backup", rel.parts[0]):
            continue
        if "__pycache__" in rel.parts or ".pytest_cache" in rel.parts or p.name in ("MANIFEST.sha256", "AGENTS.md"):
            continue
        out.append(p)
    return out


# ------------------------------------------------------------------ hermetic fixtures
@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_fixture_present(name):
    assert (FIXTURES / name).is_file(), f"fixtures/{name} missing (tests must fail, not skip)"


@pytest.mark.parametrize("name", FIXTURE_RECORDS)
def test_fixture_records_validate_via_cli_including_trace(name):
    """CLI validates with base_dir = the record's directory, so the 1.1.0 preview's trace_ref hash is checked"""
    r = subprocess.run([PY, "-B", str(SPEC / "validate_session.py"), str(FIXTURES / name)], capture_output=True,
                       text=True, env=ENV, cwd=str(SPEC))
    assert r.returncode == 0 and "OK:" in r.stdout, r.stdout + r.stderr


def test_v1_1_preview_fixture_has_relative_trace_ref():
    s = json.loads((FIXTURES / "session-2026-10-13.v1_1.json").read_text())
    assert s["trace_ref"]["path"] == "session-2026-10-13.trace.json"


def test_no_machine_paths_or_emails_in_release_files():
    bad = []
    for p in release_files():
        text = p.read_text(errors="replace")
        bad += [f"{p.relative_to(SPEC)}: {m}" for m in ABS_PATH.findall(text)]
        bad += [f"{p.relative_to(SPEC)}: {m}" for m in EMAIL.findall(text) if not EMAIL_OK.search(m)]
        if p.suffix in (".py", ".json", ".yaml"):
            assert TMP not in text, p
    assert not bad, bad


def test_no_checkout_name_dependency():
    """the spec must run from any checkout name/location (no CI symlink): no code names the consumer repo dir"""
    for p in release_files():
        if p.suffix in (".py", ".json", ".yaml"):
            assert CHECKOUT_NAME not in p.read_text(), p


def test_backups_resolve_relative_to_spec_dir():
    for p in OLD_RECORDS:
        assert p.is_file() and SPEC in p.resolve().parents, p


# ------------------------------------------------------------------ 1.1.4 = 1.1.3 checks
def _mutated(version):
    """records that break each 1.1.3 rule (same set as test_invariants_v1_1_3._mutated) labelled `version`"""
    s = fx.approved_record(); s["schema_version"] = version
    ap = lambda m: next(a for a in m["approvals"] if a["approval_id"] == "ap-1")
    pos = lambda m: next(p for p in m["paper_positions"] if p["position_id"] == "pos-1")
    rv = lambda m: next(r for r in m["risk_reviews"] if r["review_id"] == "rv-flag")
    out = [copy.deepcopy(s)]
    m = copy.deepcopy(s); ap(m)["acknowledged_flags"] = []; out.append(m)
    m = copy.deepcopy(s); a = ap(m); a["status"], a["status_changed_at"] = "revoked", fx.at("12:28"); out.append(m)
    m = copy.deepcopy(s); a = ap(m); a["status"] = "consumed"; a.pop("status_changed_at", None); out.append(m)
    m = copy.deepcopy(s); rv(m)["results"][0]["result"] = "VETO"; out.append(m)
    m = copy.deepcopy(s); m["risk_reviews"][1]["overall"].update(verdict="PASS", blocking_rule_ids=[]); out.append(m)
    m = copy.deepcopy(s); pos(m)["fills"][1]["fill_id"] = "f-1"; out.append(m)
    m = copy.deepcopy(s); pos(m)["fills"][0]["leg_id"] = "leg-9"; out.append(m)
    m = copy.deepcopy(s); ap(m)["review_id"] = "rv-postopen"; out.append(m)
    m = copy.deepcopy(s); pos(m)["fills"][0]["filled_at"] = fx.at("16:01"); out.append(m)
    return out


def test_schema_version_1_1_4_listed_and_valid():
    # spec 1.1.5: the example and VERSION moved on to 1.1.5 (test_release_v1_1_5.py); 1.1.4 stays listed and valid
    assert "1.1.4" in json.loads((SPEC / "session.schema.json").read_text())["properties"]["schema_version"]["enum"]
    s = fx.example(); s["schema_version"] = "1.1.4"
    assert vs.validate(s) == []


def test_1_1_4_has_exactly_the_1_1_3_checks():
    for r13, r14 in zip(_mutated("1.1.3"), _mutated("1.1.4")):
        assert vs.validate(r13) == vs.validate(r14)
    assert any(vs.validate(r) for r in _mutated("1.1.4")[1:])   # the mutations really fail


def test_watcher_target_does_not_depend_on_version_label():
    for version in ("1.1.3", "1.1.4"):
        s = fx.approved_record(); s["schema_version"] = version
        a = next(x for x in s["approvals"] if x["approval_id"] == "ap-1")
        a["status"], a["status_changed_at"] = "revoked", fx.at("12:40")
        assert wr.target_from_session(s, fx.TK, now="2026-10-13T13:00:00-04:00")["status"] == "rejected"


# ------------------------------------------------------------------ back-compat: frozen 1.1.3 validator
_RUNNER = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import validate_session as vs
print(json.dumps([vs.validate(r) for r in json.load(sys.stdin)]))
"""


def frozen_113(records):
    out = subprocess.run([PY, "-B", "-c", _RUNNER, str(BACKUP_113)], input=json.dumps(records), capture_output=True,
                         text=True, env=ENV, cwd=str(BACKUP_113), check=True)
    return json.loads(out.stdout)


def test_frozen_1_1_3_backup_is_the_1_1_3_release():
    assert (BACKUP_113 / "VERSION").read_text().strip() == "1.1.3"
    assert json.loads((BACKUP_113 / "session.example.json").read_text())["schema_version"] == "1.1.3"


def test_backcompat_old_records_identical_to_frozen_1_1_3():
    recs = [json.loads(p.read_text()) for p in OLD_RECORDS]   # read_text raises (FAIL) if a record is missing
    assert len(recs) == 8
    new = [vs.validate(r) for r in recs]
    assert new == frozen_113(recs) and all(e == [] for e in new)


@pytest.mark.parametrize("version", ["1.1.0", "1.1.1", "1.1.2", "1.1.3"])
def test_backcompat_mutations_identical_to_frozen_1_1_3(version):
    recs = _mutated(version)
    assert [vs.validate(r) for r in recs] == frozen_113(recs)


def test_frozen_1_1_3_rejects_1_1_4_label():
    """1.1.4 is new: the frozen validator flags it as an unknown schema_version (consumers must upgrade together)"""
    s = fx.example(); s["schema_version"] = "1.1.4"
    assert any(e.startswith("schema schema_version") for e in frozen_113([s])[0])
