"""Spec 1.1.6 (SOL-ACCEPT-001 gaps G3-G6): schema_version 1.1.6 with the new checks gated to 1.1.6 records, back-compat
with the frozen 1.1.5 release in _v1_1_5_backup (older records validate exactly as before; W / R cases unchanged), and
the rulings: G4 open approval window approved_at < t < expires_at at the fill (INV-06) and in the watcher, compared as
instants; G5 naive / unparsable times are errors with an INV id, never a crash; G6 INV-17-REUSE (one approval opens at
most one position); G3 fail-closed rule aggregate; the explicit watcher target_precedence. PAPER ONLY."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

import risk_reference as rr
import session_fixtures as fx
import test_release_v1_1_4 as r114          # module imports only: their tests are not collected twice
import test_release_v1_1_5 as r115
import test_watcher_reference as twr
import validate_session as vs
import watcher_reference as wr

SPEC = Path(__file__).resolve().parent
BACKUP_115 = SPEC / "_v1_1_5_backup"
MANIFEST_115_SHA256 = "ecc73140a23fc5b4b0e4bedf46020981bca5cefcfbda8011c3bd72ef206bd7a4"
PY = sys.executable
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
NOW = twr.SESSION_NOW
OLD_RECORDS = r115.OLD_RECORDS + [BACKUP_115 / "session.example.json"]
OLD_VERSIONS = ["1.0.0", "1.1.0", "1.1.1", "1.1.2", "1.1.3", "1.1.4", "1.1.5"]

# fixture approval ap-1: approved 12:25, expires 16:00 NY (EDT, -04:00) on 2026-10-13 = 16:25Z .. 20:00Z
APPROVED_Z = datetime(2026, 10, 13, 16, 25, tzinfo=timezone.utc)
EXPIRES_Z = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
S = timedelta(seconds=1)
CET, EST = timezone(timedelta(hours=1)), timezone(timedelta(hours=-5))

# no watcher (W) or risk (R) case outcome changed in 1.1.6 (test_*_unchanged_vs_frozen_1_1_5 below)
CHANGED_VS_1_1_5: dict = {}


# ------------------------------------------------------------------ labels
def test_schema_version_1_1_6_listed_and_example_is_1_1_6():
    enum = json.loads((SPEC / "session.schema.json").read_text())["properties"]["schema_version"]["enum"]
    assert enum[:len(OLD_VERSIONS) + 1] == OLD_VERSIONS + ["1.1.6"]   # spec 1.1.7 appended 1.1.7, 1.1.8 appended 1.1.8
    ex = fx.example(); ex["schema_version"] = "1.1.6"                  # the example tracks the current version (1.1.7 since 1.1.7, 1.1.8 since 1.1.8)
    assert vs.validate(ex) == []
    frozen = SPEC / "_v1_1_6_backup"
    assert (frozen / "VERSION").read_text().strip() == "1.1.6"
    assert yaml.safe_load((frozen / "risk_rules.yaml").read_text())["ruleset_version"] == "1.1.6"
    assert yaml.safe_load((frozen / "watcher_rules.yaml").read_text())["watcher_version"] == "1.1.6"


def test_example_regenerated_only_labels_changed():
    """the 1.1.6 example (frozen in _v1_1_6_backup) = the frozen 1.1.5 example with the three version labels bumped"""
    old = json.loads((BACKUP_115 / "session.example.json").read_text())
    new = json.loads((SPEC / "_v1_1_6_backup" / "session.example.json").read_text())
    labels = json.dumps(old).replace('"1.1.5"', '"1.1.6"')
    assert json.loads(labels) == new
    assert json.dumps(old).count('"1.1.5"') == 4


def test_schema_lists_inv17():
    inv = json.loads((SPEC / "session.schema.json").read_text())["x-invariants"]
    assert [x.split(" ", 1)[0] for x in inv] == [f"INV-{i:02d}" for i in range(1, 18)]
    assert "INV-17-REUSE" in inv[16] and "approved_at < t < expires_at" in inv[5]


# ------------------------------------------------------------------ frozen 1.1.5 release
def _frozen(script, payload, check=True):
    out = subprocess.run([PY, "-B", "-c", script, str(BACKUP_115)], input=json.dumps(payload), capture_output=True,
                         text=True, env=ENV, cwd=str(BACKUP_115), check=check)
    return json.loads(out.stdout) if check else out


_EVAL = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import risk_reference as rr
rules = rr.load_rules()
out = []
for c in rules["tests"]:
    o = rr.evaluate(rr.apply_case(rules["fixture"], c), rules, c.get("config"), profile=c.get("profile"))
    out.append([c["id"], o["results"], o["overall"]])
print(json.dumps(out, default=str))
"""


def test_frozen_1_1_5_backup_is_the_1_1_5_release():
    assert (BACKUP_115 / "VERSION").read_text().strip() == "1.1.5"
    assert json.loads((BACKUP_115 / "session.example.json").read_text())["schema_version"] == "1.1.5"
    man = (BACKUP_115 / "MANIFEST.sha256").read_bytes()
    assert hashlib.sha256(man).hexdigest() == MANIFEST_115_SHA256
    lines = man.decode().splitlines()
    assert len(lines) == 30
    for line in lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((BACKUP_115 / name).read_bytes()).hexdigest() == digest, name


def test_backup_is_not_collected_and_not_in_manifest():
    assert "_v1_1_5_backup" in __import__("conftest").collect_ignore
    assert "_v1_1_5_backup" not in (SPEC / "MANIFEST.sha256").read_text()


def test_backcompat_old_records_identical_to_frozen_1_1_5():
    recs = [json.loads(p.read_text()) for p in OLD_RECORDS]   # read_text raises (FAIL) if a record is missing
    assert len(recs) == 10
    new = [vs.validate(r) for r in recs]
    assert new == _frozen(r115._VALIDATE, recs) and all(e == [] for e in new)


def _two_positions(s, when="12:35"):
    s["paper_positions"].append(fx.position(position_id="pos-2", fills=[
        fx.fill("f-3", "leg-1", "JPM261030C00340000", "buy_to_open", 4.00, when=when),
        fx.fill("f-4", "leg-2", "JPM261030C00350000", "sell_to_open", 0.95, when=when)]))
    return s


def _mutated_1_1_6(version):
    """r114._mutated (every 1.1.3 rule broken) + the 1.1.5 tamper kinds + the cases 1.1.6 rules on, labelled `version`"""
    recs = r114._mutated(version)
    for kind in fx.TAMPER_KINDS:
        m = copy.deepcopy(recs[0])
        fx.tamper_review(next(r for r in m["risk_reviews"] if r["review_id"] == "rv-flag"), kind)
        recs.append(m)
    for when in ("16:00", "12:25", "12:24", "15:59"):                       # G4 boundaries
        m = copy.deepcopy(recs[0]); [f.update(filled_at=fx.at(when)) for f in m["paper_positions"][0]["fills"]]
        recs.append(m)
    recs.append(_two_positions(copy.deepcopy(recs[0])))                  # G6 reuse
    m = _two_positions(copy.deepcopy(recs[0])); a = m["approvals"][0]
    a["status"], a["status_changed_at"] = "consumed", fx.at("12:31"); recs.append(m)
    m = copy.deepcopy(recs[0]); m["paper_positions"][0]["fills"][0]["filled_at"]["ny"] = "2026-10-13T12:30:00"
    recs.append(m)                                                       # G5 naive (schema error)
    return recs


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_backcompat_mutations_identical_to_frozen_1_1_5(version):
    recs = _mutated_1_1_6(version)
    assert [vs.validate(r) for r in recs] == _frozen(r115._VALIDATE, recs)


def test_1_1_6_ids_equal_1_1_5_ids_except_the_rulings():
    """per mutation the error ids of a 1.1.6 record equal those of the 1.1.5 record, except the cases 1.1.6 rules on"""
    ids = lambda errs: sorted({e.split(" ", 1)[0] for e in errs})
    old, new = _mutated_1_1_6("1.1.5"), _mutated_1_1_6("1.1.6")
    diff = {}
    for i, (o, n) in enumerate(zip(old, new)):
        if ids(vs.validate(o)) != ids(vs.validate(n)):
            diff[i] = (ids(vs.validate(o)), ids(vs.validate(n)))
    k = len(r114._mutated("1.1.5")) + len(fx.TAMPER_KINDS)
    assert diff == {k: ([], ["INV-06-EXPIRED"]), k + 1: ([], ["INV-06-BEFORE"]), k + 4: ([], ["INV-17-REUSE"]),
                    k + 5: (["INV-06-STATUS"], ["INV-06-STATUS", "INV-17-REUSE"]),
                    k + 6: (["schema"], ["INV-06-TIME-NAIVE", "schema"])}, diff


def test_frozen_1_1_5_rejects_1_1_6_label():
    """1.1.6 is new: the frozen 1.1.5 validator flags it as an unknown schema_version (consumers upgrade together)"""
    assert any(e.startswith("schema schema_version") for e in _frozen(r115._VALIDATE, [fx.example()])[0])


# ------------------------------------------------------------------ W / R cases: nothing changed
def test_watcher_yaml_cases_unchanged_vs_frozen_1_1_5():
    old = yaml.safe_load((BACKUP_115 / "watcher_rules.yaml").read_text())
    assert old["tests"] == twr.CASES and len(twr.CASES) == 59
    for k in old:
        if k not in ("watcher_version",):
            assert old[k] == twr.WR[k], k
    assert set(twr.WR) - set(old) == {"real_approval"}   # 1.1.6 also added a top-level target_precedence; 1.1.7 removed it


def test_session_cases_and_grid_identical_to_frozen_1_1_5_reference():
    """all session-built W cases and the 5 x 6 x 9 grid of test_release_v1_1_5: same target dict as the frozen 1.1.5
    reference (no `errors` key appears, nothing changed: CHANGED_VS_1_1_5 is empty)"""
    cases = [c for c in twr.CASES if any(k in c for k in twr.SESSION_KEYS)]
    recs = [r115._case_record(c) for c in cases] + [s for _, s in r115._grid()]
    old = _frozen(r115._TARGET, [[s, fx.TK, NOW] for s in recs])
    new = [wr.target_from_session(s, fx.TK, now=NOW) for s in recs]
    changed = {i: (o, n) for i, (o, n) in enumerate(zip(old, new)) if o != n}
    assert len(recs) == 14 + 270 and changed == CHANGED_VS_1_1_5


def test_risk_yaml_cases_unchanged_vs_frozen_1_1_5():
    old_rules = yaml.safe_load((BACKUP_115 / "risk_rules.yaml").read_text())
    cur = rr.load_rules()
    assert old_rules["tests"] == cur["tests"] and len(cur["tests"]) == 78
    assert {k for k in cur if cur[k] != old_rules.get(k)} == {"ruleset_version", "overall"}
    old = _frozen(_EVAL, None)
    new = [[c["id"], o["results"], o["overall"]] for c in cur["tests"]
           for o in [rr.evaluate(rr.apply_case(cur["fixture"], c), cur, c.get("config"), profile=c.get("profile"))]]
    assert json.loads(json.dumps(new, default=str)) == old


# ------------------------------------------------------------------ G4: open window, validator (INV-06) and watcher
def _rec_fill(t: datetime, version="1.1.6"):
    s = fx.approved_record(); s["schema_version"] = version
    for f in s["paper_positions"][0]["fills"]:
        f["filled_at"] = rr.tspair(t)
    return s


def _inv06_window(errs):
    return sorted({e.split(" ", 1)[0] for e in errs if e.startswith(("INV-06-BEFORE", "INV-06-EXPIRED"))})


def _real_at(now: str) -> bool:
    return wr.target_from_session(fx.approved_record(with_position=False), fx.TK, now=now)["approved"]


BOUNDARIES = [  # instant, valid (approval usable), the INV-06 error otherwise
    (EXPIRES_Z, False, "INV-06-EXPIRED"), (EXPIRES_Z - S, True, None), (EXPIRES_Z + S, False, "INV-06-EXPIRED"),
    (APPROVED_Z, False, "INV-06-BEFORE"), (APPROVED_Z + S, True, None), (APPROVED_Z - S, False, "INV-06-BEFORE")]


@pytest.mark.parametrize("t,valid,err", BOUNDARIES, ids=["at-expiry", "1s-before-expiry", "1s-after-expiry",
                                                         "at-approved", "1s-after-approved", "1s-before-approved"])
def test_g4_fill_window_boundaries_validator(t, valid, err):
    assert _inv06_window(vs.validate(_rec_fill(t))) == ([] if valid else [err])


@pytest.mark.parametrize("t,valid,err", BOUNDARIES, ids=["at-expiry", "1s-before-expiry", "1s-after-expiry",
                                                         "at-approved", "1s-after-approved", "1s-before-approved"])
@pytest.mark.parametrize("tz", [timezone.utc, CET, EST, rr.NY], ids=["Z", "+01:00", "-05:00", "NY"])
def test_g4_watcher_window_boundaries_any_offset(t, valid, err, tz):
    """the same instant written as Z, +01:00, -05:00 or NY local gives the same answer as the validator at filled_at"""
    now = t.astimezone(tz).isoformat()
    assert _real_at(now) is valid, now
    assert (_inv06_window(vs.validate(_rec_fill(t))) == []) is valid


def test_g4_offsets_inside_the_validator():
    """INV-06 compares instants: approval / fill pairs written with Z, +01:00 and -05:00 (function level; the schema
    TsPair pattern only admits -04/-05 and +01/+02, so a record cannot carry Z)"""
    s = fx.approved_record(); p, a = s["paper_positions"][0], copy.deepcopy(s["approvals"][0])
    tk = next(t for t in s["tickets"] if t["ticket_id"] == fx.TK)
    rvs = {r["review_id"]: r for r in s["risk_reviews"]}
    a["approved_at"] = {"ny": "2026-10-13T16:25:00+00:00", "berlin": "2026-10-13T17:25:00+01:00"}
    a["expires_at"] = {"ny": "2026-10-13T21:00:00+01:00", "berlin": "2026-10-13T21:00:00+01:00"}
    for ny, want in (("2026-10-13T15:00:00-05:00", ["INV-06-EXPIRED"]), ("2026-10-13T14:59:59-05:00", []),
                     ("2026-10-13T20:00:00+00:00", ["INV-06-EXPIRED"]), ("2026-10-13T12:25:00-04:00", ["INV-06-BEFORE"]),
                     ("2026-10-13T17:25:01+01:00", [])):
        q = copy.deepcopy(p)
        for f in q["fills"]:
            f["filled_at"] = {"ny": ny, "berlin": ny}
        assert _inv06_window(vs.position_errors(q, a, tk, rvs)) == want, ny


def test_g4_dst_crossing_window():
    """approved in EDT (-04:00), expires after the 2026-11-01 switch in EST (-05:00): still instants"""
    s = fx.approved_record(); p, a = s["paper_positions"][0], copy.deepcopy(s["approvals"][0])
    tk = next(t for t in s["tickets"] if t["ticket_id"] == fx.TK)
    a["approved_at"] = rr.tspair(rr.ts("2026-10-30T12:00:00-04:00"))
    a["expires_at"] = rr.tspair(rr.ts("2026-11-02T10:00:00-05:00"))
    for ny, want in (("2026-11-02T15:00:00+00:00", ["INV-06-EXPIRED"]), ("2026-11-02T09:59:59-05:00", []),
                     ("2026-11-02T11:00:00-04:00", ["INV-06-EXPIRED"]), ("2026-11-02T10:59:59-04:00", []), ("2026-10-30T16:00:00+00:00", ["INV-06-BEFORE"])):
        q = copy.deepcopy(p)
        for f in q["fills"]:
            f["filled_at"] = rr.tspair(rr.ts(ny))
        assert _inv06_window(vs.position_errors(q, a, tk, {})) == want, ny
        sess = fx.approved_record(with_position=False); sess["approvals"][0].update(approved_at=a["approved_at"],
                                                                                 expires_at=a["expires_at"])
        assert wr.target_from_session(sess, fx.TK, now=ny)["approved"] is (want == []), ny


def test_g4_old_records_keep_the_closed_window():
    """1.1.3-1.1.5: approved_at <= filled_at <= expires_at (a fill exactly at either end is accepted)"""
    for t in (EXPIRES_Z, APPROVED_Z):
        assert _inv06_window(vs.validate(_rec_fill(t, "1.1.5"))) == []
    assert _inv06_window(vs.validate(_rec_fill(EXPIRES_Z + S, "1.1.5"))) == ["INV-06-EXPIRED"]
    assert _inv06_window(vs.validate(_rec_fill(APPROVED_Z - S, "1.1.5"))) == ["INV-06-BEFORE"]


def test_g4_status_change_is_strict_at_the_fill():
    """INV-06-STATUS: a status change at or before the opening fill invalidates it (all 1.1.3+ versions)"""
    for version in ("1.1.5", "1.1.6"):
        for when, bad in (("12:30", True), ("12:29", True), ("12:31", False)):
            s = fx.approved_record(); s["schema_version"] = version
            a = s["approvals"][0]; a["status"], a["status_changed_at"] = "revoked", fx.at(when)
            assert any(e.startswith("INV-06-STATUS f-1") for e in vs.validate(s)) is bad, (version, when)


# ------------------------------------------------------------------ G5: naive / unparsable times never crash
def test_g5_naive_fill_is_an_inv_error_not_a_crash(tmp_path):
    s = fx.approved_record(); s["paper_positions"][0]["fills"][0]["filled_at"]["ny"] = "2026-10-13T12:30:00"
    e = vs.validate(s)
    assert any(x.startswith("INV-06-TIME-NAIVE paper_positions/") and "fills/0 (f-1) filled_at.ny" in x for x in e), e
    f = tmp_path / "naive.json"; f.write_text(json.dumps(s))
    r = subprocess.run([PY, "-B", str(SPEC / "validate_session.py"), str(f)], capture_output=True, text=True, env=ENV)
    assert r.returncode == 1 and "INV-06-TIME-NAIVE" in r.stdout + r.stderr, r.stdout + r.stderr


def test_g5_naive_approval_is_an_inv05_error():
    s = fx.approved_record(); s["approvals"][0]["expires_at"]["berlin"] = "2026-10-13T22:00:00"
    assert any(x.startswith("INV-05-TIME-NAIVE approvals/") and "expires_at.berlin" in x for x in vs.validate(s))


def test_g5_function_level_naive_and_invalid():
    s = fx.approved_record(); p, a = s["paper_positions"][0], s["approvals"][0]
    tk = next(t for t in s["tickets"] if t["ticket_id"] == fx.TK)
    q = copy.deepcopy(p); q["fills"][0]["filled_at"] = {"ny": "2026-10-13T12:30:00", "berlin": "x"}
    q["fills"][1]["filled_at"] = {"ny": "not a time", "berlin": "x"}
    e = vs.position_errors(q, a, tk, {})
    assert [x.split(" ", 2)[:2] for x in e if "TIME" in x] == [["INV-06-TIME-NAIVE", "f-1"], ["INV-06-TIME-INVALID", "f-2"]]
    b = copy.deepcopy(a); b["approved_at"] = {"ny": "2026-10-13T12:25:00", "berlin": "x"}
    assert any(x.startswith("INV-05-TIME-NAIVE ap-1 approved_at") for x in vs.approval_errors(b, None, None))
    assert any(x.startswith("INV-06-TIME-NAIVE pos-1 approval ap-1 approved_at") for x in vs.position_errors(p, b, tk, {}))


@pytest.mark.parametrize("now,err", [("2026-10-13T13:00:00", "INV-06-TIME-NAIVE now"), ("13:00", "INV-06-TIME-INVALID now"),
                                     ("", "INV-06-TIME-INVALID now")])
def test_g5_watcher_naive_now_fails_closed(now, err):
    t = wr.target_from_session(fx.approved_record(with_position=False), fx.TK, now=now)
    assert t["approved"] is False and t["hypothetical"] is True and t["errors"][0].startswith(err), t


def test_g5_watcher_naive_approval_and_review_time_fail_closed():
    s = fx.approved_record(with_position=False); s["approvals"][0]["expires_at"]["ny"] = "2026-10-13T16:00:00"
    t = wr.target_from_session(s, fx.TK, now=NOW)
    assert not t["approved"] and t["errors"][0].startswith("INV-06-TIME-NAIVE approval ap-1 expires_at.ny"), t
    s = fx.approved_record(with_position=False)
    next(r for r in s["risk_reviews"] if r["review_id"] == "rv-flag")["evaluated_at"]["ny"] = "2026-10-13T12:21:00"
    t = wr.target_from_session(s, fx.TK, now=NOW)
    assert t["status"] == "vetoed" and t["errors"][0].startswith("INV-02-TIME-NAIVE review rv-flag"), t
    assert "errors" not in wr.target_from_session(fx.approved_record(with_position=False), fx.TK, now=NOW)


def test_g5_frozen_1_1_5_watcher_crashed_on_naive_now():
    """documents the fixed defect: the frozen reference raises (ValueError) on a naive `now`"""
    r = _frozen(r115._TARGET, [[fx.approved_record(with_position=False), fx.TK, "2026-10-13T13:00:00"]], check=False)
    assert r.returncode != 0 and "naive" in r.stderr


# ------------------------------------------------------------------ G6: INV-17 one approval opens at most one position
def test_g6_inv17_reuse():
    s = _two_positions(fx.approved_record())
    e = vs.validate(s)
    assert [x for x in e if x.startswith("INV-17")] == [
        "INV-17-REUSE approval ap-1 is referenced by 2 paper positions ['pos-1', 'pos-2']; one approval opens at most "
        "one position (the first is pos-1)"], e
    assert e == [x for x in e if x.startswith("INV-17")]          # nothing else is wrong with the record
    s["schema_version"] = "1.1.5"
    assert vs.validate(s) == []                                    # 1.1.5 records: unchanged (no INV-17)


def test_g6_consumed_approval_reused():
    s = _two_positions(fx.approved_record()); a = s["approvals"][0]
    a["status"], a["status_changed_at"] = "consumed", fx.at("12:31")
    ids = sorted({x.split(" ", 1)[0] for x in vs.validate(s)})
    assert ids == ["INV-06-STATUS", "INV-17-REUSE"]
    s = fx.approved_record(); a = s["approvals"][0]
    a["status"], a["status_changed_at"] = "consumed", fx.at("12:31")
    assert vs.validate(s) == []                                    # consumed after its one position: fine


def test_g6_three_positions_one_error():
    s = _two_positions(fx.approved_record())
    s["paper_positions"].append(fx.position(position_id="pos-3", fills=[
        fx.fill("f-5", "leg-1", "JPM261030C00340000", "buy_to_open", 4.00, when="12:40"),
        fx.fill("f-6", "leg-2", "JPM261030C00350000", "sell_to_open", 0.95, when="12:40")]))
    assert [x for x in vs.validate(s) if x.startswith("INV-17")] == [
        "INV-17-REUSE approval ap-1 is referenced by 3 paper positions ['pos-1', 'pos-2', 'pos-3']; one approval opens "
        "at most one position (the first is pos-1)"]


# ------------------------------------------------------------------ G3: fail-closed aggregate
BAD_RESULTS = ["veto", "BLOCK", None, "", "Pass", "pass ", 3]


@pytest.mark.parametrize("bad", BAD_RESULTS, ids=repr)
def test_g3_unrecognised_result_is_veto(bad):
    r = [{"rule_id": "R01", "result": "PASS", "severity": "hard"}, {"rule_id": "R02", "result": bad, "severity": "soft"}]
    assert rr.overall(r) == {"verdict": "VETO", "blocking_rule_ids": ["R02"], "flag_rule_ids": [], "approval_required": True}
    assert rr.combine(["PASS", bad]) == "VETO"
    if isinstance(bad, str) or bad is None:
        assert rr.overall_v115(r)["verdict"] == "PASS"            # the frozen aggregate failed open


@pytest.mark.parametrize("sev", [None, "missing", "", "HARD", "Soft", "medium"], ids=repr)
def test_g3_unknown_without_severity_is_hard(sev):
    r = {"rule_id": "R09", "result": "UNKNOWN"}
    if sev != "missing":
        r["severity"] = sev
    assert rr.classify(r) == "blocking" and rr.overall([r])["verdict"] == "VETO"
    assert rr.overall([{**r, "severity": "soft"}])["verdict"] == "FLAG"
    assert rr.overall([{**r, "severity": "hard"}])["verdict"] == "VETO"


def test_g3_never_pass_on_garbage():
    for results in ([], [{}], ["PASS"], [{"rule_id": "R01"}], [{"result": "PASS", "severity": "hard"}, None]):
        assert rr.overall(results)["verdict"] != "PASS" or results == [], results
    assert rr.combine([]) == "UNKNOWN"


def test_g3_identical_on_schema_valid_results():
    """for every result / severity the schema admits, the fail-closed aggregate equals the frozen one"""
    import itertools
    rows = [{"rule_id": f"R{i}", "result": res, "severity": sev}
            for i, (res, sev) in enumerate(itertools.product(rr.RESULTS, rr.SEVERITIES))]
    for n in (1, 2, 3):
        for combo in itertools.combinations(rows, n):
            assert rr.overall(combo) == rr.overall_v115(combo)


def test_g3_validator_inv16_recompute_fails_closed():
    rv = copy.deepcopy(fx.flag_review(fx.example()))
    r = next(x for x in rv["results"] if x["rule_id"] == "R11_CONCENTRATION"); r["result"] = "veto"
    e = vs.overall_errors(rv)
    assert any(x.startswith("INV-16-VERDICT rv-flag overall.verdict FLAG but rule results give VETO") for x in e), e
    assert vs.review_is_veto(rv) and wr.review_blocked(rv)
    assert not vs.review_is_veto(rv, v116=False)                   # frozen: wrong case was ignored (fail open)
    rv = copy.deepcopy(fx.flag_review(fx.example()))
    r = next(x for x in rv["results"] if x["rule_id"] == "R08_MACRO_EVENTS"); r.pop("severity")
    assert vs.review_is_veto(rv) and "R08_MACRO_EVENTS" in rr.overall(rv["results"])["blocking_rule_ids"]
    assert any(x.startswith("INV-16-VERDICT rv-flag") for x in vs.overall_errors(rv))
    rv["overall"]["verdict"] = "pass"
    assert vs.review_is_veto(rv)                                   # unrecognised stored verdict: VETO


# ------------------------------------------------------------------ explicit target_precedence
# spec 1.1.7 (Moodle) removed the duplicate top-level `target_precedence` from watcher_rules.yaml; scope.target_precedence
# is the only source. The 1.1.6 step list (step, id, target, watched) is read from the frozen 1.1.6 YAML and must still
# match the current scope list, the reference and the md numbering.
def _steps_1_1_6():
    return yaml.safe_load((SPEC / "_v1_1_6_backup" / "watcher_rules.yaml").read_text())["target_precedence"]


def test_target_precedence_is_explicit_and_matches_reference_and_scope():
    assert "target_precedence" not in twr.WR                       # 1.1.7: scope.target_precedence only
    tp = _steps_1_1_6()
    assert [x["id"] for x in tp] == list(wr.TARGET_PRECEDENCE) == twr.WR["scope"]["target_precedence"]
    assert [x["step"] for x in tp] == ["1", "2", "3", "4a", "4b"] and twr.WR["real_approval"]["step"] == "5"
    assert [x["target"] for x in tp] == ["vetoed", "idea_status", "rejected", "approved_pending", "idea_status"]
    assert [x["watched"] for x in tp] == [False, False, False, True, False]


def test_target_precedence_matches_md_numbering():
    md = (SPEC / "watcher_rules.md").read_text().splitlines()
    i = next(n for n, l in enumerate(md) if l.startswith("- **Full precedence for a ticket target**"))
    block = []
    for l in md[i + 1:]:
        if l.startswith("- "):
            break
        m = re.match(r"^  (\d)\. \*\*([^*]+)\*\*", l) or re.match(r"^     ([ab])\. ", l)
        if m:
            block.append(m.group(1))
    steps = [("4" + s if s in "ab" else s) for s in block if s != "4"]
    assert steps == ["1", "2", "3", "4a", "4b", "5"]


def _first_match(st, tamper, idea):
    """independent reading of the 1.1.6 step `when` texts, in scope.target_precedence order, for the test_release_v1_1_5 grid"""
    real = idea in ("approved_pending", "approved") and st == "active"           # step 5 (window / INV-05 / INV-16 clean)
    when = {"vetoed": tamper is not None or idea == "vetoed",
            "terminal": idea in ("executed", "closed"),
            "rejected": idea == "rejected" or (st == "revoked" and not real),
            "approved_pending": idea in ("approved_pending", "approved", None),
            "idea_status": True}
    steps = {x["id"]: x for x in _steps_1_1_6()}
    step = next(steps[i] for i in twr.WR["scope"]["target_precedence"] if when[i])
    status = idea if step["target"] == "idea_status" else step["target"]
    return step["id"], status, step["id"] == "approved_pending" and real


def test_reference_follows_target_precedence_first_match_wins():
    seen = set()
    for (st, tamper, idea), s in r115._grid():
        sid, status, approved = _first_match(st, tamper, idea)
        t = wr.target_from_session(s, fx.TK, now=NOW)
        assert (t["status"], t["approved"]) == (status, approved), (st, tamper, idea, t)
        seen.add(sid)
    assert seen == set(wr.TARGET_PRECEDENCE)                       # every step is exercised


# ------------------------------------------------------------------ hygiene of the new files
def test_new_files_hygiene():
    for name in ("test_release_v1_1_6.py", "validate_session.py", "watcher_reference.py", "risk_reference.py",
                 "watcher_rules.yaml", "risk_rules.yaml", "CHANGELOG.md"):
        text = (SPEC / name).read_text()
        assert not r114.ABS_PATH.findall(text), name
        assert all(r114.EMAIL_OK.search(m) for m in r114.EMAIL.findall(text)), name
