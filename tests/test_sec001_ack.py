"""FIX-SEC-001 PR-A (AUDIT-001B P0-1, spec 1.1.3 INV-05-ACK-*): a FLAG review's approval must acknowledge EXACTLY
the review's flag_rule_ids (none missing, none extra, none twice). An absent acknowledged_flags acknowledges nothing.
Enforced by Doodle's 1.1.3 validator and mirrored in records/guards.py for every record version; both are wired
into `optpipe validate`, validate_file, Orchestrator.validate and the watcher. Negative tests fail on 1d077a74.
PAPER ONLY: synthetic records from Doodle's session.example.json, no network."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from optpipe.cli import main
from optpipe.watcher.runtime import targets_from_session
from sec001_records import FLAGS, approved

NY = ZoneInfo("America/New_York")
TID = "tkt-jpm-bcs-340-350"
NOW = datetime(2026, 10, 13, 12, 30, tzinfo=NY)          # inside apr-1's window (12:20 to 16:00 NY)


def cli_validate(tmp_path, r, capsys) -> tuple[int, str]:
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(r))
    rc = main(["validate", str(p)])
    out = capsys.readouterr()
    return rc, out.out + out.err


def watched(r):
    return next(t for t in targets_from_session(r, NOW) if t.kind == "ticket" and t.ticket_id == TID)


def test_flag_approval_full_ack_ok(example, tmp_path, capsys):
    """Control (passes before and after): every flag acknowledged exactly once validates."""
    assert cli_validate(tmp_path, approved(example), capsys) == (0, "OK: 0 errors\n")


def test_watcher_honours_full_ack(example):
    """Control for the watcher (needs PR-B's targets_from_session(rec, now)): a fully acknowledged approval stands."""
    t = watched(approved(example))
    assert not t.hypothetical and t.status == "approved_pending", t.note


@pytest.mark.parametrize("acks", [[], None], ids=["empty", "absent"])
def test_flag_approval_without_ack_rejected(example, tmp_path, capsys, acks):
    rc, out = cli_validate(tmp_path, approved(example, acknowledged=acks), capsys)
    assert rc == 1, out
    assert ("INV-05-ACK-MISSING apr-1 does not acknowledge flagged rule_id(s) "
            "['R08_MACRO_EVENTS', 'R10_IV_VS_HISTORY'] of rv-postopen") in out
    assert out.count("INV-05-ACK-MISSING") == 1                    # spec + guards errors are merged, not doubled
    t = watched(approved(example, acknowledged=acks))
    assert t.hypothetical and "approval NOT honoured" in t.note and "INV-05-ACK-MISSING" in t.note


def test_flag_approval_partial_ack_rejected(example, tmp_path, capsys):
    rc, out = cli_validate(tmp_path, approved(example, acknowledged=FLAGS[:1]), capsys)
    assert rc == 1 and "INV-05-ACK-MISSING apr-1 does not acknowledge flagged rule_id(s) ['R10_IV_VS_HISTORY']" in out
    assert watched(approved(example, acknowledged=FLAGS[:1])).hypothetical


def test_flag_approval_extra_ack_rejected(example, tmp_path, capsys):
    rc, out = cli_validate(tmp_path, approved(example, acknowledged=[*FLAGS, "R04_MAX_LOSS"]), capsys)
    assert rc == 1 and "INV-05-ACK-EXTRA apr-1 acknowledges rule_id(s) ['R04_MAX_LOSS'] that rv-postopen did not flag" in out


def test_flag_approval_duplicate_ack_rejected(example, tmp_path, capsys):
    rc, out = cli_validate(tmp_path, approved(example, acknowledged=[*FLAGS, FLAGS[0]]), capsys)
    assert rc == 1 and "INV-05-ACK-DUPLICATE apr-1 acknowledged_flags lists ['R08_MACRO_EVENTS'] more than once" in out


def test_ack_check_is_exact_match_not_case_or_prefix(example, tmp_path, capsys):
    rc, out = cli_validate(tmp_path, approved(example, acknowledged=["r08_macro_events", "R10_IV"]), capsys)
    assert rc == 1 and "INV-05-ACK-MISSING" in out and "INV-05-ACK-EXTRA" in out
