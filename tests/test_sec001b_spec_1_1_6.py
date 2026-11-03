"""PROPOSAL for optpipe tests/ (branch fix/sec-001b, Poodle): negative tests for SOL-ACCEPT-001 gaps G3 / G4 / G5 / G6
as ruled in Doodle's spec 1.1.6. Drafted by Doodle; NOT applied to options-pipeline. Needs the pinned spec 1.1.6
(scripts/sync_spec.py re-pin) and sec001_records.py from fix/sec-001.

Ruling (spec 1.1.6): an approval is valid only while approved_at < t < expires_at (strict both ends, instants, any UTC
offset); naive / unparsable times are errors with an INV id, never a crash; one approval opens at most one paper
position (INV-17-REUSE); the rule aggregate fails closed (an unrecognised result, or UNKNOWN without severity
hard/soft, is blocking). Every test marked NEGATIVE fails on 1d077a74 and on f7a295e / 021aed2 (see Doodle's
SOL-ACCEPT-001-spec-confirmation.md for the run); CONTROL tests pass everywhere. PAPER ONLY, synthetic, no network."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

try:
    from optpipe.records import guards
except ImportError:          # 1d077a74 has no records/guards.py: every guard test fails there (NEGATIVE)
    guards = None
from optpipe.watcher.runtime import targets_from_session
from sec001_records import FLAGS, approved, with_position

NY = ZoneInfo("America/New_York")
TID = "tkt-jpm-bcs-340-350"
NOW = datetime(2026, 10, 13, 12, 30, tzinfo=NY)          # inside the default window 12:20 .. 16:00 NY
NOWS = "2026-10-13T12:30:00-04:00"


def pair(hms: str, ny_off: str = "-04:00") -> dict:
    h, m, s = map(int, hms.split(":"))
    return {"ny": f"2026-10-13T{h:02d}:{m:02d}:{s:02d}{ny_off}", "berlin": f"2026-10-13T{h + 6:02d}:{m:02d}:{s:02d}+02:00"}


@pytest.fixture(scope="module")
def ref(spec):
    sys.path.insert(0, str(spec.root))
    try:
        import validate_session
        import watcher_reference
        yield watcher_reference, validate_session
    finally:
        sys.path.remove(str(spec.root))


def rec16(example, **kw) -> dict:
    r = approved(example, **kw)
    r["schema_version"] = "1.1.6"
    return r


def fill_at(r: dict, ny: dict) -> dict:
    for f in r["paper_positions"][0]["fills"]:
        f["filled_at"] = copy.deepcopy(ny)
    return r


def ids(errs) -> set[str]:
    return {e.split(" ", 1)[0] for e in errs}


def ours(r: dict, now: datetime = NOW):
    t = next(t for t in targets_from_session(r, now) if t.kind == "ticket" and t.ticket_id == TID)
    return t.status, (t.status == "vetoed" or t.vetoed), (t.status == "approved_pending" and not t.hypothetical)


def theirs(ref, r: dict, now: str = NOWS):
    t = ref[0].target_from_session(r, TID, now)
    return t["status"], t["status"] == "vetoed", bool(t["approved"])


def test_pinned_spec_is_1_1_6(spec):                                       # NEGATIVE (pin)
    assert (spec.root / "VERSION").read_text().strip() == "1.1.6"


# ---------------------------------------------------------------- G4: INV-06 fill window (open at both ends)
FILL_CASES = [  # filled_at, expected window error ids (approval 12:20:00 .. 16:00:00 NY)
    ("at_expiry", pair("16:00:00"), {"INV-06-EXPIRED"}),                    # NEGATIVE (021aed2 accepts it)
    ("1s_before_expiry", pair("15:59:59"), set()),                          # CONTROL
    ("1s_after_expiry", pair("16:00:01"), {"INV-06-EXPIRED"}),              # CONTROL (rejected since 1.1.3)
    ("at_approved_at", pair("12:20:00"), {"INV-06-BEFORE"}),                # NEGATIVE (021aed2 accepts it)
    ("1s_after_approved_at", pair("12:20:01"), set()),                      # CONTROL
    ("1s_before_approved_at", pair("12:19:59"), {"INV-06-BEFORE"}),         # CONTROL
    ("at_expiry_written_Z", {"ny": "2026-10-13T20:00:00+00:00", "berlin": "2026-10-13T22:00:00+02:00"}, {"INV-06-EXPIRED"}),
    ("at_expiry_written_+01:00", {"ny": "2026-10-13T21:00:00+01:00", "berlin": "2026-10-13T22:00:00+02:00"}, {"INV-06-EXPIRED"}),
    ("at_expiry_written_-05:00", {"ny": "2026-10-13T15:00:00-05:00", "berlin": "2026-10-13T22:00:00+02:00"}, {"INV-06-EXPIRED"}),
    ("1s_before_expiry_written_Z", {"ny": "2026-10-13T19:59:59+00:00", "berlin": "2026-10-13T21:59:59+02:00"}, set()),
]


@pytest.mark.parametrize("name,when,want", FILL_CASES, ids=[c[0] for c in FILL_CASES])
def test_guard_fill_window_boundaries(example, name, when, want):
    r = fill_at(with_position(rec16(example)), when)
    p, a = r["paper_positions"][0], r["approvals"][0]
    got = ids(guards.position_errors(p, a)) & {"INV-06-BEFORE", "INV-06-EXPIRED"}
    assert got == want


@pytest.mark.parametrize("name,when,want", FILL_CASES[:6], ids=[c[0] for c in FILL_CASES[:6]])
def test_check_record_and_spec_validator_agree_on_fill_window(example, ref, name, when, want):
    r = fill_at(with_position(rec16(example)), when)
    window = {"INV-06-BEFORE", "INV-06-EXPIRED"}
    assert ids(guards.check_record(r)) & window == want
    assert ids(ref[1].validate(copy.deepcopy(r))) & window == want


# ---------------------------------------------------------------- G4: watcher window (open at both ends) + parity
WATCH_CASES = [  # approval times, now = 12:30:00 NY; expected "really approved"
    ("expires_at_now", {"expires_at": pair("12:30:00")}, False),
    ("expires_1s_after_now", {"expires_at": pair("12:30:01")}, True),
    ("expires_1s_before_now", {"expires_at": pair("12:29:59")}, False),
    ("approved_at_now", {"approved_at": pair("12:30:00")}, False),          # NEGATIVE (021aed2: real)
    ("approved_1s_before_now", {"approved_at": pair("12:29:59")}, True),
    ("approved_at_now_written_-05:00", {"approved_at": {"ny": "2026-10-13T11:30:00-05:00",
                                                        "berlin": "2026-10-13T18:30:00+02:00"}}, False),  # NEGATIVE
    ("expires_at_now_written_Z", {"expires_at": {"ny": "2026-10-13T16:30:00+00:00",
                                                 "berlin": "2026-10-13T18:30:00+02:00"}}, False),
]


@pytest.mark.parametrize("name,upd,real", WATCH_CASES, ids=[c[0] for c in WATCH_CASES])
def test_watcher_window_boundaries_match_reference(example, ref, name, upd, real):
    r = approved(example); r["approvals"][0].update(copy.deepcopy(upd))
    assert ours(r)[2] is real
    assert ours(r) == theirs(ref, r)


# ---------------------------------------------------------------- G5: naive / unparsable times never crash
def test_naive_filled_at_is_error_not_crash(example):                       # NEGATIVE (021aed2: TypeError)
    r = with_position(rec16(example)); r["paper_positions"][0]["fills"][0]["filled_at"]["ny"] = "2026-10-13T12:30:00"
    assert "INV-06-TIME-NAIVE" in ids(guards.check_record(r))


def test_naive_approved_at_is_error_not_crash(example):                     # NEGATIVE
    r = with_position(rec16(example)); r["approvals"][0]["approved_at"]["ny"] = "2026-10-13T12:20:00"
    errs = guards.check_record(r)
    assert ids(errs) & {"INV-05-TIME-NAIVE", "INV-06-TIME-NAIVE"}


@pytest.mark.parametrize("field", ["approved_at", "expires_at"])
def test_watcher_naive_approval_time_is_hypothetical_not_crash(example, ref, field):   # NEGATIVE (TypeError)
    r = approved(example); r["approvals"][0][field]["ny"] = r["approvals"][0][field]["ny"][:19]
    assert ours(r) == theirs(ref, r) == ("approved_pending", False, False)


def test_watcher_naive_review_time_is_vetoed_not_crash(example, ref):      # NEGATIVE (TypeError)
    r = approved(example); r["risk_reviews"][-1]["evaluated_at"]["ny"] = r["risk_reviews"][-1]["evaluated_at"]["ny"][:19]
    assert ours(r) == theirs(ref, r) == ("vetoed", True, False)


def test_validate_cli_naive_fill_exit_1_with_id(example, tmp_path):         # NEGATIVE (021aed2: traceback)
    r = with_position(rec16(example)); r["paper_positions"][0]["fills"][0]["filled_at"]["ny"] = "2026-10-13T12:30:00"
    f = tmp_path / "naive.json"; f.write_text(json.dumps(r))
    p = subprocess.run([sys.executable, "-m", "optpipe", "validate", str(f)], capture_output=True, text=True)
    assert p.returncode != 0 and "Traceback" not in p.stderr and "INV-06-TIME-NAIVE" in p.stdout + p.stderr


# ---------------------------------------------------------------- G6: one approval opens at most one position
def _two_positions(r: dict) -> dict:
    p2 = copy.deepcopy(r["paper_positions"][0]); p2["position_id"] = "pos-2"
    for f in p2["fills"]:
        f["fill_id"] = f["fill_id"] + "b"
    r["paper_positions"].append(p2)
    return r


def test_one_approval_two_positions_rejected(example, ref):                 # NEGATIVE (021aed2: exit 0)
    r = _two_positions(with_position(rec16(example)))
    assert "INV-17-REUSE" in ids(guards.check_record(r))
    assert "INV-17-REUSE" in ids(ref[1].validate(copy.deepcopy(r)))


def test_consumed_approval_second_position_rejected(example):               # NEGATIVE
    r = _two_positions(with_position(rec16(example)))
    a = r["approvals"][0]; a["status"], a["status_changed_at"] = "consumed", pair("12:31:00")   # consumed by pos-1
    assert "INV-17-REUSE" in ids(guards.check_record(r))


def test_one_position_per_approval_ok(example, ref):                        # CONTROL
    r = with_position(rec16(example))
    assert guards.check_record(r) == [] and ref[1].validate(copy.deepcopy(r)) == []


# ---------------------------------------------------------------- G3: fail-closed aggregate
@pytest.mark.parametrize("bad", ["veto", "BLOCK", None, "", "Pass"], ids=repr)
def test_effective_verdict_unknown_result_string_is_veto(example, ref, bad):    # NEGATIVE
    r = approved(example); x = next(x for x in r["risk_reviews"][-1]["results"] if x["severity"] == "hard")
    x["result"] = bad
    assert guards.effective_verdict(r["risk_reviews"][-1]) == "VETO"
    assert ours(r) == theirs(ref, r) == ("vetoed", True, False)


@pytest.mark.parametrize("sev", ["missing", None, "HARD", "x"], ids=repr)
def test_unknown_without_severity_is_blocking(example, ref, sev):           # NEGATIVE
    r = approved(example); x = next(x for x in r["risk_reviews"][-1]["results"] if x["severity"] == "hard")
    x["result"] = "UNKNOWN"
    if sev == "missing":
        x.pop("severity")
    else:
        x["severity"] = sev
    assert guards.classify(x) == guards.BLOCKING
    assert guards.effective_verdict(r["risk_reviews"][-1]) == "VETO"
    assert ours(r) == theirs(ref, r) == ("vetoed", True, False)


def test_required_acks_unknown_severity(example):                           # NEGATIVE
    rv = copy.deepcopy(approved(example)["risk_reviews"][-1])
    x = next(x for x in rv["results"] if x["rule_id"] == FLAGS[0]); x.pop("severity")
    assert guards.effective_verdict(rv) == "VETO"                  # a blocked review needs no ack: it cannot be approved


def test_guards_aggregate_equals_reference_on_schema_valid_results(ref, spec):   # CONTROL
    import itertools
    sys.path.insert(0, str(spec.root))
    try:
        import risk_reference as rr
    finally:
        sys.path.remove(str(spec.root))
    rows = [{"rule_id": f"R{i}", "result": a, "severity": b}
            for i, (a, b) in enumerate(itertools.product(["PASS", "FLAG", "UNKNOWN", "VETO"], ["hard", "soft"]))]
    for n in (1, 2):
        for combo in itertools.combinations(rows, n):
            o = rr.overall(list(combo))
            d = guards.derive_overall(combo)
            assert (d["verdict"], sorted(d["blocking_rule_ids"]), sorted(d["flag_rule_ids"])) == \
                   (o["verdict"], sorted(o["blocking_rule_ids"]), sorted(o["flag_rule_ids"]))
