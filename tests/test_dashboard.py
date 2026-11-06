"""Milestone 2 dashboard: FastAPI TestClient over a fixture out/ dir (offline, read-only, GET only)."""
import json
from pathlib import Path

import pytest

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from optpipe.cli import main as cli_main  # noqa: E402
from optpipe.dashboard import create_app  # noqa: E402
from optpipe.dashboard.app import timeline_model, tickets_model  # noqa: E402
from optpipe.dashboard.data import OutDir, berlin, clock, pair  # noqa: E402

DATE = "2026-10-13"
SV = "1.1.6"                     # Doodle's spec 1.1.6: the record is written as 1.1.6 (with trace_ref)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("dash") / "out"
    assert cli_main(["run", "--out", str(d)]) == 0
    assert cli_main(["run", "--out", str(d), "--append", "--as-of", "2026-10-13T09:40:00-04:00"]) == 0
    return d


@pytest.fixture(scope="module")
def client(out):
    return TestClient(create_app(out))


@pytest.fixture(scope="module")
def v11(out):
    return json.loads((out / f"session-{DATE}.json").read_text())


def test_all_routes_are_get_only(out):
    app = create_app(out)
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert len(routes) >= 9
    for r in routes:
        assert r.methods <= {"GET", "HEAD"}, (r.path, r.methods)


def test_write_methods_rejected(client):
    for m in ("post", "put", "patch", "delete"):
        assert getattr(client, m)(f"/s/{DATE}/today").status_code == 405


def test_index_redirects_to_latest(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"/s/{DATE}/today"


def test_empty_out_dir(tmp_path):
    r = TestClient(create_app(tmp_path)).get("/")
    assert r.status_code == 200 and "No sessions yet" in r.text


def test_paper_banner_and_healthz(client):
    assert client.get("/healthz").text.startswith("ok")
    for page in ("today", "tickets", "timeline"):
        html = client.get(f"/s/{DATE}/{page}").text
        assert "PAPER ONLY" in html and "no broker, no orders" in html
    assert "PAPER ONLY" in client.get("/history").text


def test_today_page(client, v11):
    html = client.get(f"/s/{DATE}/today").text
    assert "run-open-0940" in html and "Global snapshot" in html and "Premarket scan" in html
    assert "Nikkei 225" in html and "JPM" in html and "ev-fomc-2026-10-28" in html
    # per-run selection (ruling 4: scans/snapshots keyed by run_id)
    html_pm = client.get(f"/s/{DATE}/today?run=run-premarket").text
    assert 'class="pill on" href="/s/2026-10-13/today?run=run-premarket"' in html_pm
    assert client.get(f"/s/{DATE}/today?run=nope").status_code == 404
    assert client.get("/s/2026-10-14/today").status_code == 404
    assert client.get("/s/not-a-date/today").status_code == 404


def test_tickets_page_rule_by_rule(client, v11):
    html = client.get(f"/s/{DATE}/tickets").text
    assert "HYPOTHETICAL - paper only, not an order" in html
    for t in v11["tickets"]:
        assert t["ticket_id"] in html
    latest = [r for r in v11["risk_reviews"] if r["run_id"] == "run-open-0940"]
    for rv in latest:
        assert rv["review_id"] in html
        for res in rv["results"]:
            assert res["rule_id"] in html
    assert "R01_QUOTE_FRESHNESS" in html and 'class="badge VETO"' in html
    assert "raw 3.1006 → down to 0.05 tick" in html            # ruling 3 raw + rounded visible
    assert 'hx-get="/s/2026-10-13/review/' in html             # earlier runs load via HTMX


def test_review_partial(client, v11):
    rv = v11["risk_reviews"][0]
    r = client.get(f"/s/{DATE}/review/{rv['review_id']}")
    assert r.status_code == 200 and "<html" not in r.text
    assert rv["overall"]["verdict"] in r.text and all(x["rule_id"] in r.text for x in rv["results"])
    assert client.get(f"/s/{DATE}/review/rv-nope").status_code == 404


def test_timeline_page_and_step_partial(client, out):
    trace = json.loads((out / f"session-{DATE}.trace.json").read_text())
    html = client.get(f"/s/{DATE}/timeline").text
    for run in trace["runs"]:
        assert run["run_id"] in html and run["recommendation"] in html
        for st in run["steps"]:
            assert f'hx-get="/s/{DATE}/step/{run["run_id"]}/{st["seq"]}"' in html
    assert "sidecar ✓" in html and "trace_ref ✓" in html and "session_ref ✗" not in html
    for a in ("DiscoveryAgent", "ResearchAgent", "PlannerAgent", "RiskOfficerAgent", "WatcherAgent"):
        assert a in html
    r = client.get(f"/s/{DATE}/step/run-premarket/4")
    assert r.status_code == 200 and "RiskOfficerAgent" in r.text and "tkt-jpm-bcs-340-350" in r.text
    assert client.get(f"/s/{DATE}/step/run-premarket/99").status_code == 404


def test_timeline_model_geometry(out):
    s = OutDir(out).load(DATE)
    runs = timeline_model(s)
    assert [r["run"]["run_id"] for r in runs] == ["run-premarket", "run-open-0940"]
    for r in runs:
        assert [st["seq"] for st in r["steps"]] == sorted(st["seq"] for st in r["steps"])
        assert r["steps"][0]["left_pct"] == 0
        assert all(0 <= st["left_pct"] <= 100 and st["width_pct"] > 0 for st in r["steps"])
        assert len(r["handoffs"]) == len(r["steps"]) - 1
        assert r["verdicts"] == {"VETO": 4}
    assert [len(t["reviews"]) for t in tickets_model(s)] == [2, 2, 2, 2]
    assert all(t["latest"]["run_id"] == "run-open-0940" for t in tickets_model(s))


def test_history_and_idea_log(client, v11):
    html = client.get("/history").text
    assert f'href="/s/{DATE}/today"' in html
    for i in v11["idea_log"]:
        assert i["idea_id"] in html
    assert "8 VETO" in html                                     # 4 tickets x 2 runs
    assert "/files/session-2026-10-13.trace.json" in html


def test_raw_files_only_session_files(client, out):
    r = client.get(f"/files/session-{DATE}.json")
    assert r.status_code == 200 and r.json()["schema_version"] == SV
    for bad in ("../pyproject.toml", "settings.yaml", "session-x.py", "..%2F..%2Fetc%2Fpasswd"):
        assert client.get(f"/files/{bad}").status_code == 404


def test_integrity_detects_tampering(out, tmp_path):
    import shutil
    d = tmp_path / "o"
    shutil.copytree(out, d)
    tp = d / f"session-{DATE}.trace.json"
    tp.write_text(tp.read_text().replace("NO TRADE", "NO-TRADE", 1))
    s = OutDir(d).load(DATE)
    assert not s.integrity.ok and s.integrity.sidecar_ok is False and s.integrity.trace_ref_ok is False
    html = TestClient(create_app(d)).get(f"/s/{DATE}/timeline").text
    assert "sidecar ✗" in html and "check failed" in html


def test_outdir_rejects_sidecar_symlink_outside_root(out, tmp_path):
    import shutil
    d = tmp_path / "o"
    shutil.copytree(out, d)
    (d / f"session-{DATE}.watch.json").write_text(json.dumps({"session": {}}))
    outside = tmp_path / "outside.sha256"
    outside.write_text("external sidecar")
    (d / f"session-{DATE}.watch.json.sha256").symlink_to(outside)

    with pytest.raises(KeyError):
        OutDir(d).load(DATE)


def test_v1_only_session_is_upgraded(out, tmp_path):
    import shutil
    d = tmp_path / "o"
    shutil.copytree(out, d)
    from optpipe.records.compat import to_v1
    jp = d / f"session-{DATE}.json"
    jp.write_text(json.dumps(to_v1(json.loads(jp.read_text()))))     # an older, v1-only output
    s = OutDir(d).load(DATE)
    assert s.origin.startswith("v1 ") and s.record["schema_version"].startswith("1.1")
    assert TestClient(create_app(d)).get(f"/s/{DATE}/tickets").status_code == 200


def test_time_formatting():
    assert pair({"ny": "2026-10-13T07:52:00-04:00", "berlin": "2026-10-13T13:52:00+02:00"}) == \
        "2026-10-13 07:52 ET (13:52 Berlin)"
    assert berlin("2026-10-29T19:11:58Z") == "2026-10-29 20:11 CET"
    assert berlin("2026-10-13T07:52:00-04:00") == "2026-10-13 13:52 CEST"
    assert clock("2026-10-29T19:11:58.199940Z") == "20:11:58.199 CET"


def test_static_export(tmp_path):
    import subprocess, sys
    dest = tmp_path / "docs"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_dashboard.py"), "--dest", str(dest), "--no-png"],
                       capture_output=True, text=True, env={**__import__("os").environ, "PYTHONWARNINGS": "ignore"})
    assert r.returncode == 0, r.stderr
    for page in ("today", "tickets", "timeline", "history"):
        html = (dest / f"{page}.html").read_text()
        assert "PAPER ONLY" in html and "hx-get" not in html and "/static/htmx" not in html
        assert 'href="/s/' not in html and "/tmp/" not in html
    assert 'href="timeline.html"' in (dest / "today.html").read_text()
    assert "<details><summary>#4 RiskOfficerAgent inputs / outputs" in (dest / "timeline.html").read_text()
    assert "SHADOW_EOD" in (dest / "today.html").read_text()          # M3: export includes the replay watch run
    assert 'id="watcher-lane"' in (dest / "timeline.html").read_text()
