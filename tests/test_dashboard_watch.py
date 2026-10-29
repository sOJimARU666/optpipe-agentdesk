"""M3 dashboard integration: watcher alerts on the Today page and as lane 5 of the timeline (GET only, offline)."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from optpipe.cli import main as cli_main
from optpipe.dashboard import create_app
from optpipe.dashboard.app import watch_model
from optpipe.dashboard.data import OutDir

DATE = "2026-10-13"
ROOT = Path(__file__).resolve().parents[1]
TAPE = ROOT / "tests" / "fixtures" / DATE / "watch_tape.yaml"


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("dashw") / "out"
    assert cli_main(["run", "--out", str(d)]) == 0
    assert cli_main(["run", "--out", str(d), "--append", "--as-of", "2026-10-13T09:40:00-04:00"]) == 0
    assert cli_main(["watch", "--session", str(d / f"session-{DATE}.json"), "--out", str(d),
                     "--replay", str(TAPE)]) == 0
    return d


@pytest.fixture(scope="module")
def client(out):
    return TestClient(create_app(out))


def test_session_loads_watch_and_checks_integrity(out):
    s = OutDir(out).load(DATE)
    assert s.watch and s.watch["watch_version"] == "watch-1.0"
    assert s.files["watch"] == f"session-{DATE}.watch.json"
    assert s.integrity.watch_sidecar_ok is True and s.integrity.watch_session_ok is True
    assert s.integrity.ok


def test_watch_model_lane_geometry(out):
    m = watch_model(OutDir(out).load(DATE))
    assert m["summary"]["alerts"] == len(m["alerts"]) == 4
    day = next(d for d in m["days"] if d["date"] == DATE)
    assert not day["closed"] and day["open"]["ny"].endswith("09:30:00-04:00") and day["close"]["ny"].endswith("16:00:00-04:00")
    # SHADOW_EOD alerts sit exactly on the close line; the 15:55 shadow quote wake is just before it
    assert {a["left_pct"] for a in day["markers"]} == {day["close_pct"]}
    q = [w for w in day["wakes"] if w["quote_calls"]]
    assert len(q) == 1 and q[0]["left_pct"] < day["close_pct"]
    assert all(0 <= w["left_pct"] <= 100 for w in day["wakes"])


def test_today_panel(client):
    html = client.get(f"/s/{DATE}/today").text
    assert "Watcher alerts" in html and html.count("SHADOW_EOD") >= 4
    assert "VETOED · shadow" in html and "16:00 NY" in html and "22:00 Berlin" in html
    assert "HAND-MADE FICTIONAL" in html and "sidecar ✓" in html and "session sha ✓" in html


def test_timeline_lane5(client):
    html = client.get(f"/s/{DATE}/timeline").text
    assert 'id="watcher-lane"' in html and "Lane 5 · Watcher" in html
    assert html.count('class="wdot L-info"') == 4 and "09:30 NY open" in html and "15:30 Berlin" in html


def test_raw_watch_files_served(client):
    r = client.get(f"/files/session-{DATE}.watch.json")
    assert r.status_code == 200 and r.json()["label"].startswith("PAPER ONLY")
    assert client.get(f"/files/session-{DATE}.watch.json.sha256").status_code == 200


def test_no_watch_file_message(tmp_path, out):
    d = tmp_path / "out"
    shutil.copytree(out, d)
    for p in d.glob("*.watch.json*"):
        p.unlink()
    c = TestClient(create_app(d))
    assert "No watcher output for this session" in c.get(f"/s/{DATE}/today").text
    assert "No watcher output for this session" in c.get(f"/s/{DATE}/timeline").text


def test_tampered_watch_is_reported(tmp_path, out):
    d = tmp_path / "out"
    shutil.copytree(out, d)
    wp = d / f"session-{DATE}.watch.json"
    doc = json.loads(wp.read_text()); doc["alerts"] = []
    wp.write_text(json.dumps(doc))
    s = OutDir(d).load(DATE)
    assert s.integrity.watch_sidecar_ok is False and not s.integrity.ok
    assert "sidecar ✗" in TestClient(create_app(d)).get(f"/s/{DATE}/today").text


def test_stale_watch_vs_session_is_reported(tmp_path, out):
    d = tmp_path / "out"
    shutil.copytree(out, d)
    sp = d / f"session-{DATE}.json"
    sp.write_text(sp.read_text() + "\n")              # session changed after the watch run
    s = OutDir(d).load(DATE)
    assert s.integrity.watch_session_ok is False
    assert any("re-run optpipe watch" in p for p in s.integrity.problems)


def test_watch_doc_paths_are_not_absolute(out):
    doc = json.loads((out / f"session-{DATE}.watch.json").read_text())
    assert not doc["session"]["path"].startswith("/") and not doc["mode"]["tape"].startswith("/")
