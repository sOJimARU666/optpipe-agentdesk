"""SOL-ACCEPT-001 G4 + G6 (spec 1.1.6): the INV-06 fill window and the watcher's approval window are OPEN at both
ends (approved_at < t < expires_at, instants compared whatever the UTC offset), 1.1.3-1.1.5 records keep their frozen
closed window, and one approval opens at most one paper position (INV-17-REUSE, validator side only).

Behaviour-only: the window / reuse checks call optpipe's own guard layer (`optpipe.records.guards.check_record`, what
`optpipe validate`, the dashboard and the watcher consumers merge with the validator) so they do not pass merely
because the pinned spec validator already rejects the record; controls also go through `optpipe validate`
(optpipe.cli.main). The watcher is reached through `targets_from_session`; records are built from spec/session.example.json with tests/sec001_records.py (approval
12:20:00 .. 16:00:00 NY). Packet names; `test_fill_exactly_at_approved_at_ok` is flipped and renamed
`test_fill_exactly_at_approved_at_rejected` (Moodle decision 1: spec 1.1.6 makes it INV-06-BEFORE).
PAPER ONLY, synthetic, no network."""
from __future__ import annotations

import copy
import inspect
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from optpipe.cli import main
from optpipe.watcher.runtime import targets_from_session
from sec001_records import approved, with_position

NY = ZoneInfo("America/New_York")
TID = "tkt-jpm-bcs-340-350"
WINDOW = {"INV-06-BEFORE", "INV-06-EXPIRED"}


def pair(ny: str, berlin_h_shift: int = 6) -> dict:
    """TsPair on 2026-10-13 from an NY 'HH:MM:SS' written with -04:00."""
    h, m, s = map(int, ny.split(":"))
    return {"ny": f"2026-10-13T{h:02d}:{m:02d}:{s:02d}-04:00",
            "berlin": f"2026-10-13T{h + berlin_h_shift:02d}:{m:02d}:{s:02d}+02:00"}


def _ids(text: str) -> set[str]:
    return {w for w in text.replace(":", " ").split() if w.startswith("INV-")}


def _validate(tmp_path, capsys, rec) -> tuple[int, set[str], str]:
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(rec, indent=2))
    capsys.readouterr()
    rc = main(["validate", str(p)])
    o = capsys.readouterr()
    return rc, _ids(o.out + o.err), o.out + o.err


def _guard(rec) -> tuple[set[str], list[str]]:
    from optpipe.records.guards import check_record      # 1d077a74 has no guard layer (ImportError there)
    errs = check_record(copy.deepcopy(rec))
    return {e.split(" ", 1)[0] for e in errs}, errs


def _rec(example, filled_at: dict, version: str = "1.1.6") -> dict:
    r = with_position(approved(example))
    r["schema_version"] = version
    for f in r["paper_positions"][0]["fills"]:
        f["filled_at"] = copy.deepcopy(filled_at)
    return r


def _targets(rec, now):
    if "now" in inspect.signature(targets_from_session).parameters:
        return targets_from_session(rec, now)
    return targets_from_session(rec)


def _really_approved(rec, now) -> bool:
    t = next(t for t in _targets(rec, now) if t.kind == "ticket" and t.ticket_id == TID)
    return t.status == "approved_pending" and not t.hypothetical


# ------------------------------------------------------------------------------------------- G4: INV-06 window
def test_fill_exactly_at_expiry_rejected(example, tmp_path, capsys):
    ids, errs = _guard(_rec(example, pair("16:00:00")))
    assert "INV-06-EXPIRED" in ids, errs


def test_fill_1s_before_expiry_ok(example, tmp_path, capsys):
    ids, errs = _guard(_rec(example, pair("15:59:59")))
    assert not ids & WINDOW, errs
    rc, ids, out = _validate(tmp_path, capsys, _rec(example, pair("15:59:59")))
    assert rc == 0 and not ids & WINDOW, out


def test_fill_1s_after_expiry_rejected(example, tmp_path, capsys):
    ids, errs = _guard(_rec(example, pair("16:00:01")))
    assert "INV-06-EXPIRED" in ids, errs


def test_fill_1s_before_approved_at_rejected(example, tmp_path, capsys):
    ids, errs = _guard(_rec(example, pair("12:19:59")))
    assert "INV-06-BEFORE" in ids, errs


def test_fill_exactly_at_approved_at_rejected(example, tmp_path, capsys):
    """Flipped from the packet's test_fill_exactly_at_approved_at_ok (spec 1.1.6, Moodle decision 1)."""
    ids, errs = _guard(_rec(example, pair("12:20:00")))
    assert "INV-06-BEFORE" in ids, errs
    rc, ids, out = _validate(tmp_path, capsys, _rec(example, pair("12:20:00")))
    assert rc == 1 and "INV-06-BEFORE" in ids, out


@pytest.mark.parametrize("off", ["+00:00", "+02:00"])
def test_fill_utc_offset_equivalent_instant(example, tmp_path, capsys, off):
    """16:00:00 NY (-04:00) written in another offset is still exactly at expiry -> INV-06-EXPIRED; 1 s earlier OK."""
    h = 20 if off == "+00:00" else 22
    at = {"ny": f"2026-10-13T{h}:00:00{off}", "berlin": "2026-10-13T22:00:00+02:00"}
    before = {"ny": f"2026-10-13T{h - 1}:59:59{off}", "berlin": "2026-10-13T21:59:59+02:00"}
    ids, errs = _guard(_rec(example, at))
    assert "INV-06-EXPIRED" in ids, errs
    ids, errs = _guard(_rec(example, before))
    assert not ids & WINDOW, errs


@pytest.mark.parametrize("edge", ["approved_at", "expires_at"])
@pytest.mark.parametrize("delta", ["at", "-1s", "+1s"])
def test_watcher_and_inv06_agree_at_boundaries(example, tmp_path, capsys, edge, delta):
    """At each boundary instant t, 'a fill at t is valid' (INV-06) == 'really approved at now=t' (watcher)."""
    base = "12:20:00" if edge == "approved_at" else "16:00:00"
    h, m, s = map(int, base.split(":"))
    sec = h * 3600 + m * 60 + s + {"at": 0, "-1s": -1, "+1s": 1}[delta]
    hms = f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
    ids, out = _guard(_rec(example, pair(hms)))
    fill_ok = not ids & WINDOW
    now = datetime(2026, 10, 13, sec // 3600, sec % 3600 // 60, sec % 60, tzinfo=NY)
    assert fill_ok is _really_approved(approved(example), now), (hms, out)
    assert fill_ok is (delta == ("+1s" if edge == "approved_at" else "-1s"))


@pytest.mark.parametrize("old", ["1.1.3", "1.1.4", "1.1.5"])
@pytest.mark.parametrize("at", ["12:20:00", "16:00:00"])
def test_old_record_keeps_closed_window(example, tmp_path, capsys, old, at):
    """Control: a 1.1.3-1.1.5 record with a fill exactly at approved_at / expires_at still validates (frozen rules)."""
    ids, errs = _guard(_rec(example, pair(at), version=old))
    assert not ids & WINDOW, errs
    rc, ids, out = _validate(tmp_path, capsys, _rec(example, pair(at), version=old))
    assert rc == 0 and not ids & WINDOW, out


def test_watcher_naive_now_is_not_real_approval(example):
    """Fail closed: a naive `now` never honours an approval (and never raises)."""
    assert _really_approved(approved(example), datetime(2026, 10, 13, 12, 30, tzinfo=NY)) is True
    assert _really_approved(approved(example), datetime(2026, 10, 13, 12, 30)) is False


def test_watcher_undated_review_of_ticket_is_vetoed(example):
    """Spec 1.1.6: a review of the ticket whose evaluated_at is naive cannot be ordered -> the ticket is vetoed."""
    r = approved(example)
    rv = copy.deepcopy(r["risk_reviews"][-1]); rv["review_id"] = "rv-naive"
    rv["evaluated_at"] = {"ny": rv["evaluated_at"]["ny"][:19], "berlin": rv["evaluated_at"]["berlin"][:19]}
    r["risk_reviews"].insert(0, rv)
    t = next(t for t in _targets(r, datetime(2026, 10, 13, 12, 30, tzinfo=NY)) if t.kind == "ticket" and t.ticket_id == TID)
    assert t.status == "vetoed"


# ------------------------------------------------------------------------------------------- G6: INV-17-REUSE
def _two_positions(r: dict) -> dict:
    p2 = copy.deepcopy(r["paper_positions"][0]); p2["position_id"] = "pos-2"
    for f in p2["fills"]:
        f["fill_id"] += "b"
    r["paper_positions"].append(p2)
    return r


def test_one_approval_two_positions_inv17_reuse(example, tmp_path, capsys):
    r = _two_positions(_rec(example, pair("12:30:00")))
    ids, errs = _guard(r)
    assert "INV-17-REUSE" in ids, errs
    rc, ids, out = _validate(tmp_path, capsys, r)
    assert rc == 1 and "INV-17-REUSE" in ids, out


def test_consumed_approval_second_position_inv17_reuse(example, tmp_path, capsys):
    r = _two_positions(_rec(example, pair("12:30:00")))
    r["approvals"][0].update(status="consumed", status_changed_at=pair("12:31:00"))
    ids, errs = _guard(r)
    assert "INV-17-REUSE" in ids, errs


@pytest.mark.parametrize("old", ["1.1.3", "1.1.4", "1.1.5"])
def test_relabelled_old_record_two_positions_inv17_reuse(example, tmp_path, capsys, old):
    """Negative (#17 review): relabelling a record 1.1.3 / 1.1.4 / 1.1.5 must not let one approval open two paper
    positions: optpipe's guards report INV-17-REUSE for every schema_version and `optpipe validate` exits 1."""
    r = _two_positions(_rec(example, pair("12:30:00"), version=old))
    ids, errs = _guard(r)
    assert "INV-17-REUSE" in ids, errs
    rc, ids, out = _validate(tmp_path, capsys, r)
    assert rc == 1 and "INV-17-REUSE" in ids, out


@pytest.mark.parametrize("at", ["12:20:00", "16:00:00"])
def test_relabelled_old_record_keeps_closed_window_with_inv17(example, at):
    """Control: running INV-17 on a 1.1.5 record leaves its frozen closed window alone (a fill exactly at
    approved_at / expires_at is still not a window error); only the reuse is reported."""
    ids, errs = _guard(_two_positions(_rec(example, pair(at), version="1.1.5")))
    assert not ids & WINDOW and ids == {"INV-17-REUSE"}, errs


def test_one_position_per_approval_ok(example, tmp_path, capsys):
    """Control."""
    ids, errs = _guard(_rec(example, pair("12:30:00")))
    assert errs == [], errs
    rc, ids, out = _validate(tmp_path, capsys, _rec(example, pair("12:30:00")))
    assert rc == 0 and not ids, out


def test_inv17_is_validator_only_watcher_unchanged(example):
    """G6 is validator-only: a second position does not change the ticket's watcher target (positions are watched)."""
    now = datetime(2026, 10, 13, 12, 30, tzinfo=NY)
    one = with_position(approved(example)); two = _two_positions(copy.deepcopy(one))
    kinds = lambda r: sorted((t.kind, t.status) for t in _targets(r, now))  # noqa: E731
    assert kinds(two) == sorted(kinds(one) + [("position", "open")])
