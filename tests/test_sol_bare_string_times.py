"""SOL-ACCEPT-001 follow-up (Moodle, #11 review): an approval time that is a bare string, not a TsPair object, is
reported as INV-05-TIME-INVALID only. The optpipe guards used to add misleading co-errors: INV-05-EXPIRY ("expires_at
is not after approved_at", when the order is unknown, not wrong) and, on every opening fill, INV-06-BEFORE /
INV-06-EXPIRED. The record stays invalid and the watcher still refuses the approval and the position (fail closed).

Behaviour through the public `optpipe validate` CLI and `watcher.runtime.targets_from_session`, on records built from
Doodle's spec/session.example.json with tests/sec001_records.py. PAPER ONLY."""
import copy
import json
import re
from datetime import datetime, timezone

import pytest

from optpipe.cli import main
from optpipe.watcher.runtime import targets_from_session
from sec001_records import approved, ts, with_position

NOW = datetime(2026, 10, 13, 16, 31, tzinfo=timezone.utc)      # 12:31 NY: inside apr-1's 12:20-16:00 window
BARE = {"approved_at": "2026-10-13T12:20:00-04:00", "expires_at": "2026-10-13T16:00:00-04:00"}
LINE_ID = re.compile(r"^\s*(INV-\d{2}(?:-[A-Z0-9]+)*)\b")


def _validate(tmp_path, rec, capsys):
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(rec, indent=2))
    capsys.readouterr()
    rc = main(["validate", str(p)])
    o = capsys.readouterr()
    text = o.out + o.err
    return rc, {m.group(1) for ln in text.splitlines() if (m := LINE_ID.match(ln))}, text


def _rec(example, field, positioned):
    r = approved(copy.deepcopy(example))
    if positioned:
        r = with_position(r)
    r["approvals"][0][field] = BARE[field]
    return r


@pytest.mark.parametrize("positioned", [False, True], ids=["approval", "approval+position"])
@pytest.mark.parametrize("field", ["approved_at", "expires_at"])
def test_bare_string_time_reports_only_inv05_time_invalid(tmp_path, capsys, example, field, positioned):
    rc, ids, text = _validate(tmp_path, _rec(example, field, positioned), capsys)
    assert rc == 1, text
    assert ids == {"INV-05-TIME-INVALID"}, f"expected only INV-05-TIME-INVALID, got {sorted(ids)}:\n{text}"


@pytest.mark.parametrize("field", ["approved_at", "expires_at"])
def test_bare_string_time_still_fails_closed_in_the_watcher(example, field):
    """Control: dropping the co-errors must not open anything: the approval is not honoured and the position is
    not watched as open."""
    ts_ = targets_from_session(_rec(example, field, True), NOW)
    tickets = [t for t in ts_ if t.kind == "ticket" and t.status == "approved_pending" and not t.hypothetical]
    positions = [(t.target_id, t.status) for t in ts_ if t.kind == "position"]
    assert tickets == [], [t.note for t in tickets]
    assert positions and all(st != "open" for _, st in positions), positions


def test_control_valid_record_has_no_inv_errors(tmp_path, capsys, example):
    rc, ids, text = _validate(tmp_path, with_position(approved(copy.deepcopy(example))), capsys)
    assert rc == 0 and not ids, text


def test_control_reversed_window_is_still_inv05_expiry(tmp_path, capsys, example):
    """Control: a real (TsPair) expires_at before approved_at is still INV-05-EXPIRY."""
    r = approved(copy.deepcopy(example))
    r["approvals"][0]["expires_at"] = ts("12:10")
    rc, ids, text = _validate(tmp_path, r, capsys)
    assert rc == 1 and "INV-05-EXPIRY" in ids, text
