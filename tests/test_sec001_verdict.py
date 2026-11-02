"""FIX-SEC-001 PR-A (AUDIT-001B P0-2, spec 1.1.3 INV-16 / INV-05-VETO): a review's stored overall verdict must equal
what its rule results give, and every consumer treats a tampered or empty review as VETO (records/guards.py
effective_verdict): the idea log, the watcher agent, the watch runtime, the CLI summary and `optpipe validate`.
The risk engine's combine([]) is UNKNOWN, never PASS. Negative tests fail on 1d077a74. PAPER ONLY, no network."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from optpipe.cli import main
from optpipe.watcher.runtime import targets_from_session
from sec001_records import approved

NY = ZoneInfo("America/New_York")
TID = "tkt-jpm-bcs-340-350"
NOW = datetime(2026, 10, 13, 12, 30, tzinfo=NY)          # inside apr-1's window (12:20 to 16:00 NY)


def tampered_pass(example) -> dict:
    """The example with an approved review whose hard rule R01 is VETO but whose overall says PASS."""
    r = approved(example, acknowledged=[])
    rv = r["risk_reviews"][-1]
    for x in rv["results"]:
        x["result"] = "PASS"
    rv["results"][0]["result"] = "VETO"
    rv["overall"].update(verdict="PASS", blocking_rule_ids=[], flag_rule_ids=[], approval_required=True)
    return r


def cli_validate(tmp_path, r, capsys) -> tuple[int, str]:
    p = tmp_path / "session-2026-10-13.json"
    p.write_text(json.dumps(r))
    rc = main(["validate", str(p)])
    out = capsys.readouterr()
    return rc, out.out + out.err


def test_combine_empty_not_pass():
    from optpipe.risk.engine import combine
    assert combine([]) == "UNKNOWN"
    assert combine([]) != "PASS"


def test_tampered_pass_review_is_veto_everywhere(example, tmp_path, capsys):
    r = tampered_pass(example)
    rv = r["risk_reviews"][-1]
    # optpipe validate: record errors, exit 1
    rc, out = cli_validate(tmp_path, r, capsys)
    assert rc == 1 and "INV-16-VERDICT rv-postopen overall.verdict PASS but rule results give VETO" in out
    # idea log: the ticket is vetoed, not approved_pending
    from optpipe.records.idea_log import apply_reviews
    il = copy.deepcopy(r["idea_log"])
    apply_reviews(il, [copy.deepcopy(rv)], datetime(2026, 10, 13, 12, 0, tzinfo=NY))
    assert next(i for i in il if i.get("ticket_id") == TID)["current_status"] == "vetoed"
    # watcher agent: no watcher rules for a vetoed ticket
    from optpipe.models.contracts import WatcherInput
    from optpipe.watcher import WatcherAgent
    out_rules = WatcherAgent().run(WatcherInput(tickets=r["tickets"], risk_reviews=r["risk_reviews"]))
    assert all(w["target"]["ticket_id"] != TID for w in out_rules.model_dump(mode="json")["watcher_rules"])
    # watch runtime: vetoed, the approval is not honoured
    t = next(x for x in targets_from_session(r, NOW) if x.kind == "ticket" and x.ticket_id == TID)
    assert t.vetoed and t.status == "vetoed", t.note


def test_approval_on_tampered_review_rejected(example, tmp_path, capsys):
    for r in (tampered_pass(example), approved(example)):
        if r["risk_reviews"][-1]["overall"]["verdict"] == "FLAG":
            r["risk_reviews"][-1]["results"][0]["result"] = "VETO"      # overall still FLAG
        rc, out = cli_validate(tmp_path, r, capsys)
        assert rc == 1 and "INV-05-VETO apr-1 approves review rv-postopen whose verdict is VETO" in out, out


def test_empty_review_is_veto(example, tmp_path, capsys):
    r = approved(example, acknowledged=[])
    rv = r["risk_reviews"][-1]
    rv["results"] = []
    rv["overall"].update(verdict="PASS", blocking_rule_ids=[], flag_rule_ids=[])
    rc, out = cli_validate(tmp_path, r, capsys)
    assert rc == 1 and "INV-16-EMPTY" in out, out
    t = next(x for x in targets_from_session(r, NOW) if x.kind == "ticket" and x.ticket_id == TID)
    assert t.vetoed, t.note
