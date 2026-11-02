"""FIX-SEC-001 PR-A (AUDIT-001B P0-3, spec 1.1.3 INV-06): a paper position's opening fill is valid only while its
approval stands: status `active`, or a non-active status (revoked, expired, consumed alike) whose status_changed_at
is STRICTLY after the fill. changed == fill is rejected (exact-equality boundary); an undated non-active approval is
rejected. Approval state comes only from `status`. Negative tests fail on 1d077a74. PAPER ONLY, no network."""
from __future__ import annotations

import json

import pytest

from optpipe.cli import main
from sec001_records import approved, ts, with_position


def cli_validate(tmp_path, r, capsys) -> tuple[int, str]:
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(r))
    rc = main(["validate", str(p)])
    out = capsys.readouterr()
    return rc, out.out + out.err


STATUSES = ["revoked", "expired", "consumed"]


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("changed", ["12:25", "12:30"], ids=["before_fill", "equal_fill"])
def test_fill_on_non_active_approval_rejected(example, tmp_path, capsys, status, changed):
    r = with_position(approved(example, status=status, status_changed_at=ts(changed)), fill_at="12:30")
    rc, out = cli_validate(tmp_path, r, capsys)
    assert rc == 1, out
    assert (f"INV-06-STATUS f-1 filled at 2026-10-13T12:30:00-04:00 but approval apr-1 became '{status}' "
            f"at 2026-10-13T{changed}:00-04:00") in out


@pytest.mark.parametrize("status", STATUSES)
def test_exact_equality_boundary_rejected(example, tmp_path, capsys, status):
    """status_changed_at == filled_at, to the second: rejected for every non-active status (was accepted for
    `consumed` on 1d077a74's guard draft and never checked by the 1.1.2 validator)."""
    r = with_position(approved(example, status=status, status_changed_at=ts("12:30")), fill_at="12:30")
    assert r["approvals"][0]["status_changed_at"] == r["paper_positions"][0]["fills"][0]["filled_at"]
    rc, out = cli_validate(tmp_path, r, capsys)
    assert rc == 1 and "INV-06-STATUS f-1" in out and "INV-06-STATUS f-2" in out


@pytest.mark.parametrize("status", STATUSES)
def test_undated_non_active_approval_rejected(example, tmp_path, capsys, status):
    r = with_position(approved(example, status=status))
    rc, out = cli_validate(tmp_path, r, capsys)
    assert rc == 1 and f"INV-06-STATUS-UNDATED pos-1 approval apr-1 status '{status}' without status_changed_at" in out


@pytest.mark.parametrize("status", STATUSES)
def test_status_changed_after_fill_ok(example, tmp_path, capsys, status):
    """Control: the approval became non-active strictly after the fill (12:31 > 12:30): valid."""
    r = with_position(approved(example, status=status, status_changed_at=ts("12:31")), fill_at="12:30")
    assert cli_validate(tmp_path, r, capsys) == (0, "OK: 0 errors\n")


def test_active_approval_fill_ok(example, tmp_path, capsys):
    assert cli_validate(tmp_path, with_position(approved(example)), capsys) == (0, "OK: 0 errors\n")


def test_status_not_decision_decides(example, tmp_path, capsys):
    """A non-schema `decision: approved` does not make a revoked approval stand (schema: additionalProperties)."""
    r = with_position(approved(example, status="revoked", status_changed_at=ts("12:25")))
    r["approvals"][0]["decision"] = "approved"
    rc, out = cli_validate(tmp_path, r, capsys)
    assert rc == 1 and "INV-06-STATUS f-1" in out


def test_guard_helper_boundary():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from optpipe.records.guards import approval_active_at
    at = datetime(2026, 10, 13, 12, 30, tzinfo=ZoneInfo("America/New_York"))
    for s in STATUSES:
        assert approval_active_at({"status": s, "status_changed_at": ts("12:30")}, at) is False
        assert approval_active_at({"status": s, "status_changed_at": ts("12:31")}, at) is True
        assert approval_active_at({"status": s}, at) is False
    assert approval_active_at({"status": "active"}, at) is True
