"""SOL-ACCEPT-001 G1 + G2: every display path shows the verdict and the blocking / flag rule_ids RECOMPUTED from
the rule results, never the stored `overall` (INV-16), and the dashboard runs the record guards.

Behaviour-only: the records are the tree's own offline run output (`pipeline_output`), tampered here, and are read
back through the public surfaces (the dashboard app, `optpipe show`, the run summary). No guards-module API is used,
so on a tree without the fix these tests fail on what is DISPLAYED, not on an import.
"""
import copy
import json
import re
import shutil

import pytest
from fastapi.testclient import TestClient

from optpipe.cli import main, summary
from optpipe.dashboard import create_app
from optpipe.records.store import read_trace

DATE = "2026-10-13"
TID = "tkt-jpm-bcs-340-350"
RID = "rv-premarket-jpm-bcs-340-350"
BLOCKING = ["R01_QUOTE_FRESHNESS", "R02_BID_ASK_WIDTH", "R03_OPEN_INTEREST", "R09_EARLY_ASSIGNMENT"]
FLAGS = ["R08_MACRO_EVENTS", "R10_IV_VS_HISTORY"]


def _review(rec):
    return next(r for r in rec["risk_reviews"] if r["review_id"] == RID)


def _tamper(rec, **overall):
    rec = copy.deepcopy(rec)
    rv = _review(rec)
    assert [x["rule_id"] for x in rv["results"] if x["result"] == "VETO"], "fixture premise: the review has VETO results"
    rv["overall"].update(overall)
    return rec


def _write(tmp_path, pipeline_output, rec):
    out = tmp_path / "out"
    out.mkdir()
    src = pipeline_output["out"]
    for p in src.iterdir():
        if p.name.startswith(f"session-{DATE}.trace"):
            shutil.copy(p, out / p.name)
    (out / f"session-{DATE}.json").write_text(json.dumps(rec, indent=2))
    return out


def _card(html, tid=TID):
    i = html.index(f'id="{tid}"')
    j = html.find('<div class="card"', i + 1)
    return html[i: j if j > 0 else len(html)]


def _verdict_badges(html):
    """The verdict badges a reader sees: the ticket heading and each review's header line (not the per-rule rows)."""
    h2 = re.findall(r'<h2[^>]*>\s*<span class="badge (\w+)">', html)
    rv = re.findall(r'<p style="margin:6px 0"><span class="badge (\w+)">', html)
    return h2 + rv


PASS_OVER_VETO = dict(verdict="PASS", blocking_rule_ids=[], flag_rule_ids=[])
IDS_STRIPPED = dict(verdict="VETO", blocking_rule_ids=[], flag_rule_ids=[])


# --- G1: dashboard -------------------------------------------------------------------------------------------------
def test_dashboard_tickets_tampered_pass_shows_recomputed_veto(tmp_path, pipeline_output):
    out = _write(tmp_path, pipeline_output, _tamper(pipeline_output["record"], **PASS_OVER_VETO))
    r = TestClient(create_app(out)).get(f"/s/{DATE}/tickets")
    assert r.status_code == 200
    card = _card(r.text)
    badges = _verdict_badges(card)
    assert badges and "PASS" not in badges, f"dashboard shows the stored PASS over VETO rule results: {badges}"
    assert set(badges) == {"VETO"}, badges
    assert "INV-16-VERDICT" in card


def test_dashboard_review_fragment_tampered_pass_shows_recomputed_veto(tmp_path, pipeline_output):
    out = _write(tmp_path, pipeline_output, _tamper(pipeline_output["record"], **PASS_OVER_VETO))
    r = TestClient(create_app(out)).get(f"/s/{DATE}/review/{RID}")
    assert r.status_code == 200
    badges = _verdict_badges(r.text)
    assert badges == ["VETO"], f"review fragment shows the stored verdict over VETO rule results: {badges}"
    assert "INV-16-VERDICT" in r.text


def test_dashboard_blocking_and_flag_ids_are_recomputed(tmp_path, pipeline_output):
    out = _write(tmp_path, pipeline_output, _tamper(pipeline_output["record"], **IDS_STRIPPED))
    card = _card(TestClient(create_app(out)).get(f"/s/{DATE}/tickets").text)
    pills = re.findall(r'<span class="pill">([A-Z0-9_]+)</span>', card)
    for rid in BLOCKING + FLAGS:
        assert rid in pills, f"{rid} missing: dashboard shows the stored (emptied) rule_id lists"


def test_dashboard_empty_review_is_not_pass(tmp_path, pipeline_output):
    rec = _tamper(pipeline_output["record"], **PASS_OVER_VETO)
    _review(rec)["results"] = []
    out = _write(tmp_path, pipeline_output, rec)
    card = _card(TestClient(create_app(out)).get(f"/s/{DATE}/tickets").text)
    badges = _verdict_badges(card)
    assert badges and set(badges) == {"VETO"}, f"a review with no rule results is shown as {badges}"
    assert "INV-16-EMPTY" in card


def test_dashboard_integrity_lists_guard_errors(tmp_path, pipeline_output):
    out = _write(tmp_path, pipeline_output, _tamper(pipeline_output["record"], **PASS_OVER_VETO))
    html = TestClient(create_app(out)).get("/history").text
    row = html[html.index(f">{DATE}</a>"):]
    row = row[: row.index("</tr>")]
    assert "✓ sha256" not in row, "history marks a guard-failing record as intact"
    assert "INV-16-VERDICT" in row, "the integrity problems do not list the guard errors"


def test_dashboard_untampered_record_has_no_guard_problems(tmp_path, pipeline_output):
    out = _write(tmp_path, pipeline_output, copy.deepcopy(pipeline_output["record"]))
    c = TestClient(create_app(out))
    assert "INV-16" not in _card(c.get(f"/s/{DATE}/tickets").text)
    row = c.get("/history").text
    assert "guard:" not in row


# --- G2: optpipe show (Markdown) and the run summary ---------------------------------------------------------------
def _show(tmp_path, pipeline_output, rec, capsys):
    out = _write(tmp_path, pipeline_output, rec)
    capsys.readouterr()
    rc = main(["show", str(out / f"session-{DATE}.json")])
    return rc, capsys.readouterr().out


def _md_block(md):
    i = md.index(f"`{RID}`")
    j = md.find("\n### ", i)
    return md[i - 4: j if j > 0 else len(md)]


def test_show_tampered_pass_prints_recomputed_veto(tmp_path, pipeline_output, capsys):
    rc, md = _show(tmp_path, pipeline_output, _tamper(pipeline_output["record"], **PASS_OVER_VETO), capsys)
    assert rc == 0
    blk = _md_block(md)
    assert "-> **PASS**" not in blk, "`optpipe show` prints the stored PASS over VETO rule results"
    assert "-> **VETO**" in blk and "INV-16-VERDICT" in blk


def test_show_blocking_and_flag_ids_are_recomputed(tmp_path, pipeline_output, capsys):
    rc, md = _show(tmp_path, pipeline_output, _tamper(pipeline_output["record"], **IDS_STRIPPED), capsys)
    line = next(ln for ln in _md_block(md).splitlines() if ln.startswith("- blocking:"))
    assert f"blocking: {', '.join(BLOCKING)};" in line, line
    assert f"flags: {', '.join(FLAGS)};" in line, line


def test_run_summary_ids_are_recomputed(pipeline_output):
    rec = _tamper(pipeline_output["record"], **IDS_STRIPPED)
    trace = read_trace(pipeline_output["out"] / f"session-{DATE}.trace.json")
    line = next(ln for ln in summary(rec, trace).splitlines() if TID in ln)
    assert "<- " + ", ".join(BLOCKING) in line, line
    assert "flags " + ", ".join(FLAGS) in line, line


def test_run_summary_tampered_pass_prints_veto(pipeline_output):
    rec = _tamper(pipeline_output["record"], **PASS_OVER_VETO)
    trace = read_trace(pipeline_output["out"] / f"session-{DATE}.trace.json")
    line = next(ln for ln in summary(rec, trace).splitlines() if TID in ln)
    assert line.split()[0] == "VETO", line
