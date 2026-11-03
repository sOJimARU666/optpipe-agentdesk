"""Spec 1.1.5: schema_version 1.1.5 = 1.1.3 / 1.1.4 checks (no new invariants), back-compat with the frozen 1.1.4
release in _v1_1_4_backup, and the watcher rulings (numbered precedence: INV-16 inconsistent review = VETO first; executed /
closed ideas keep their status; revoked approval = rejected for every pre-execution idea; a real approval needs an
approved_pending / approved idea), with the exact list of intentionally changed watcher outcomes. PAPER ONLY."""
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

import session_fixtures as fx
import test_release_v1_1_4 as r114          # module import only: its tests are not collected twice
import test_watcher_reference as twr
import validate_session as vs
import watcher_reference as wr

SPEC = Path(__file__).resolve().parent
BACKUP_114 = SPEC / "_v1_1_4_backup"
MANIFEST_114_SHA256 = "f58db767b1c631d9d7c08d15df1e7bbedaa2215c5b2efa3b1a5f4b596a2d2610"
PY = sys.executable
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
NOW = twr.SESSION_NOW
OLD_RECORDS = r114.OLD_RECORDS + [BACKUP_114 / "session.example.json"]

# watcher cases whose outcome changed on purpose in 1.1.5 (all new; none of the 48 frozen 1.1.4 cases changed)
CHANGED_VS_1_1_4 = {"SCOPE-i": "approved_pending", "SCOPE-j": "approved_pending", "SCOPE-k": "approved_pending",
                    "SCOPE-l": "proposed", "SCOPE-m": "proposed", "SCOPE-q": "simulated"}   # case id -> target status under the 1.1.4 reference


# ------------------------------------------------------------------ 1.1.5 = 1.1.4 checks
def test_schema_version_1_1_5_listed_and_example_is_1_1_5():
    enum = json.loads((SPEC / "session.schema.json").read_text())["properties"]["schema_version"]["enum"]
    assert enum[5:7] == ["1.1.4", "1.1.5"]                 # spec 1.1.6 appended 1.1.6, 1.1.7 appended 1.1.7, 1.1.8 appended 1.1.8
    ex = fx.example(); ex["schema_version"] = "1.1.5"     # the example tracks the current version (1.1.6 since 1.1.6, 1.1.7 since 1.1.7, 1.1.8 since 1.1.8)
    assert vs.validate(ex) == []
    assert (BACKUP_114.parent / "_v1_1_5_backup" / "VERSION").read_text().strip() == "1.1.5"


def test_1_1_5_has_exactly_the_1_1_4_checks():
    for r14, r15 in zip(r114._mutated("1.1.4"), r114._mutated("1.1.5")):
        assert vs.validate(r14) == vs.validate(r15)
    assert any(vs.validate(r) for r in r114._mutated("1.1.5")[1:])   # the mutations really fail


def test_inv16_tamper_is_a_record_error_only_from_1_1_3():
    for version in ("1.1.2", "1.1.3", "1.1.4", "1.1.5"):
        s = fx.approved_record(); s["schema_version"] = version
        fx.tamper_review(next(r for r in s["risk_reviews"] if r["review_id"] == "rv-flag"), "pass_over_flag")
        assert any(e.startswith("INV-16-VERDICT rv-flag") for e in vs.validate(s)) == (version != "1.1.2")


# ------------------------------------------------------------------ frozen 1.1.4 release
_VALIDATE = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import validate_session as vs
print(json.dumps([vs.validate(r) for r in json.load(sys.stdin)]))
"""

_TARGET = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import watcher_reference as wr
print(json.dumps([wr.target_from_session(s, t, now=n) for s, t, n in json.load(sys.stdin)]))
"""


def _frozen(script, payload):
    out = subprocess.run([PY, "-B", "-c", script, str(BACKUP_114)], input=json.dumps(payload), capture_output=True,
                         text=True, env=ENV, cwd=str(BACKUP_114), check=True)
    return json.loads(out.stdout)


def test_frozen_1_1_4_backup_is_the_1_1_4_release():
    assert (BACKUP_114 / "VERSION").read_text().strip() == "1.1.4"
    assert json.loads((BACKUP_114 / "session.example.json").read_text())["schema_version"] == "1.1.4"
    man = (BACKUP_114 / "MANIFEST.sha256").read_bytes()
    assert hashlib.sha256(man).hexdigest() == MANIFEST_114_SHA256
    lines = man.decode().splitlines()
    assert len(lines) == 29
    for line in lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((BACKUP_114 / name).read_bytes()).hexdigest() == digest, name


def test_backcompat_old_records_identical_to_frozen_1_1_4():
    recs = [json.loads(p.read_text()) for p in OLD_RECORDS]   # read_text raises (FAIL) if a record is missing
    assert len(recs) == 9
    new = [vs.validate(r) for r in recs]
    assert new == _frozen(_VALIDATE, recs) and all(e == [] for e in new)


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.1.1", "1.1.2", "1.1.3", "1.1.4"])
def test_backcompat_mutations_identical_to_frozen_1_1_4(version):
    recs = r114._mutated(version)
    for kind in fx.TAMPER_KINDS:
        m = copy.deepcopy(recs[0])
        fx.tamper_review(next(r for r in m["risk_reviews"] if r["review_id"] == "rv-flag"), kind)
        recs.append(m)
    assert [vs.validate(r) for r in recs] == _frozen(_VALIDATE, recs)


def test_frozen_1_1_4_rejects_1_1_5_label():
    """1.1.5 is new: the frozen 1.1.4 validator flags it as an unknown schema_version (consumers upgrade together)"""
    assert any(e.startswith("schema schema_version") for e in _frozen(_VALIDATE, [fx.example()])[0])


# ------------------------------------------------------------------ watcher: what changed vs 1.1.4
def _frozen_yaml():
    return yaml.safe_load((BACKUP_114 / "watcher_rules.yaml").read_text())


def test_the_48_frozen_cases_are_unchanged_and_keep_their_outcomes():
    old = _frozen_yaml()["tests"]
    assert len(old) == 48
    cur = {c["id"]: c for c in twr.CASES}
    for c in old:
        assert cur[c["id"]] == c, c["id"]          # same case, same expectation
        twr.test_watcher_case(c)                   # and the 1.1.5 reference still produces it
    assert sorted(set(cur) - {c["id"] for c in old}) == [f"SCOPE-{x}" for x in "hijklmnopqr"]


def test_only_scope_and_version_changed_in_watcher_rules_yaml():
    old, cur = _frozen_yaml(), twr.WR
    assert set(cur) - set(old) <= {"real_approval"}   # spec 1.1.6 added real_approval (its top-level target_precedence was removed in 1.1.7)
    assert set(old) <= set(cur)
    for k in old:
        if k not in ("watcher_version", "scope", "tests"):
            assert old[k] == cur[k], k
    assert {k: v for k, v in cur["scope"].items() if k != "target_precedence"} == old["scope"]


def _case_record(c):
    s = fx.approved_record(with_position=False)
    a = next(x for x in s["approvals"] if x["approval_id"] == "ap-1")
    st = c.get("approval_status", "active")
    if st == "none":
        s["approvals"].remove(a)
    elif st != "active":
        a["status"], a["status_changed_at"] = st, fx.at("12:40")
    if c.get("review_tamper"):
        fx.tamper_review(next(r for r in s["risk_reviews"] if r["review_id"] == "rv-flag"), c["review_tamper"])
    if c.get("idea_status"):
        next(i for i in s["idea_log"] if i["ticket_id"] == fx.TK)["current_status"] = c["idea_status"]
    return s


def test_changed_session_cases_vs_frozen_1_1_4_reference():
    """every session-built case is resolved by both references; the 1.1.5 outcome differs exactly for CHANGED_VS_1_1_4"""
    cases = [c for c in twr.CASES if any(k in c for k in twr.SESSION_KEYS)]
    old = _frozen(_TARGET, [[_case_record(c), fx.TK, NOW] for c in cases])
    changed = {}
    for c, o in zip(cases, old):
        new = wr.target_from_session(_case_record(c), fx.TK, now=NOW)
        assert new["status"] == c["expect"]["target_status"], c["id"]
        if (o["status"], o["approved"], o["hypothetical"]) != (new["status"], new["approved"], new["hypothetical"]):
            changed[c["id"]] = o["status"]
    assert changed == CHANGED_VS_1_1_4


IDEAS = ["proposed", "approved_pending", "approved", "simulated", "executed", "closed", "vetoed", "rejected", None]
APPROVALS = ["active", "revoked", "expired", "consumed", "none"]
TAMPERS = [None, *fx.TAMPER_KINDS]


def _grid():
    out = []
    for st in APPROVALS:
        for tamper in TAMPERS:
            for idea in IDEAS:
                s = _case_record({"approval_status": st, "review_tamper": tamper})
                i = next(x for x in s["idea_log"] if x["ticket_id"] == fx.TK)
                if idea is None:
                    s["idea_log"].remove(i)
                else:
                    i["current_status"] = idea
                out.append(((st, tamper, idea), s))
    return out


def test_grid_differs_from_1_1_4_only_where_the_rulings_say():
    """5 approval statuses x 6 review variants x 9 idea statuses: every difference from the frozen 1.1.4 reference is
    ruling 1 (INV-16-only tamper, no derived VETO -> vetoed, VETO first, so also over a rejected or executed idea),
    step 3 (revoked + idea proposed / simulated -> rejected; executed / closed keep their status as in 1.1.4) or step 5
    (active approval with no idea -> hypothetical, no approved idea); everything else is identical."""
    grid = _grid()
    old = _frozen(_TARGET, [[s, fx.TK, NOW] for _, s in grid])
    inv16_only = {"pass_over_flag", "flag_ids_mismatch", "blocking_ids_extra"}
    n_changed = 0
    for ((st, tamper, idea), s), o in zip(grid, old):
        new = wr.target_from_session(s, fx.TK, now=NOW)
        if idea == "vetoed" or (tamper and tamper not in inv16_only):
            expect_change = False                      # already vetoed in 1.1.4 (derived VETO or idea vetoed)
        elif tamper in inv16_only:
            expect_change = True                       # ruling 1: now vetoed (was real / hypothetical / rejected / pass-through)
        elif idea == "rejected":
            expect_change = False                      # idea rejected -> rejected, as in 1.1.4
        else:
            expect_change = ((st == "revoked" and idea in ("proposed", "simulated"))    # step 3
                             or (st == "active" and idea is None))                      # step 5
        changed = (o["status"], o["approved"], o["hypothetical"]) != (new["status"], new["approved"], new["hypothetical"])
        assert changed == expect_change, (st, tamper, idea, o, new)
        if tamper in inv16_only:
            assert new["status"] == "vetoed"           # VETO first: even over a rejected idea or a revoked approval
        if changed:
            n_changed += 1
            assert new["status"] in ("vetoed", "rejected", "approved_pending") and not new["approved"]   # fail closed only
            assert new["status"] != "approved_pending" or (o["approved"] and new["hypothetical"])    # real -> what-if
        if idea in ("executed", "closed") and tamper not in inv16_only and not (tamper and tamper not in inv16_only):
            assert new["status"] == idea, (st, tamper, idea, new)                  # step 2: terminal keeps its status
    assert n_changed == 5 * 3 * 8 + 2 + 1  # ruling 1: 120 combos (all but idea vetoed); step 3: revoked x proposed /
                                           # simulated; step 5: active x no idea


def test_watcher_target_does_not_depend_on_version_label():
    for version in ("1.0.0", "1.1.3", "1.1.4", "1.1.5"):
        s = _case_record({"approval_status": "revoked", "idea_status": "proposed"}); s["schema_version"] = version
        assert wr.target_from_session(s, fx.TK, now=NOW)["status"] == "rejected"
        s = _case_record({"review_tamper": "pass_over_flag"}); s["schema_version"] = version
        assert wr.target_from_session(s, fx.TK, now=NOW)["status"] == "vetoed"
