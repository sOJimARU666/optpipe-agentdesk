"""Milestone 2 dashboard: FastAPI TestClient over a fixture out/ dir (offline, read-only, GET only)."""
import json
import os
import signal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from optpipe.cli import main as cli_main  # noqa: E402
from optpipe.dashboard import create_app  # noqa: E402
from optpipe.dashboard.app import timeline_model, tickets_model  # noqa: E402
from optpipe.dashboard.data import OutDir, berlin, clock, pair  # noqa: E402

DATE = "2026-10-13"
SV = "1.1.8"                     # Doodle's spec 1.1.8: the record is written as 1.1.8 (with trace_ref)
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


def test_today_conflicts_retain_evidence(client):
    page = BeautifulSoup(client.get(f"/s/{DATE}/today").content, "html.parser")
    earnings = next(p for p in page.find_all("p") if p.b and p.b.get_text() == "JPM")
    kospi = next(row for row in page.select("tr") if "KOSPI" in row.stripped_strings)
    for node, observations in [
        (earnings, ["-3.7 · src-wsj-jpm · 2026-10-13T07:03:00-04:00",
                    "0.78 · src-nasdaq-jpm-info-0752 · 2026-10-13T07:52:00-04:00"]),
        (kospi, ["-7.4 · src-ilsole · time unknown", "-5.5 · src-eme · time unknown"]),
    ]:
        text = node.get_text(" ", strip=True)
        assert "conflicting" in text
        for observation in observations:
            assert observation in text


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


@pytest.mark.parametrize("date", ["../pyproject.toml", "..", "2026/10/13", "2026-10-13/../../outside", "2026-10-13\n"])
def test_outdir_rejects_non_date_path(tmp_path, date):
    outside = tmp_path / "session-2026-10-13.json"
    outside.write_text('{"marker":"TRAVERSAL-MARKER"}')
    nested = tmp_path / "nested" / "out"
    nested.mkdir(parents=True)
    with pytest.raises(KeyError):
        OutDir(nested).load(date)
    assert OutDir(nested).dates() == []
    assert "TRAVERSAL-MARKER" in outside.read_text()


def test_outside_session_symlink_is_omitted_and_rejected(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    marker = "OUTSIDE-SESSION-MARKER"
    outside = tmp_path / "outside.json"
    outside.write_text('{"marker":"' + marker + '"}')
    (out / f"session-{DATE}.json").symlink_to(Path("..") / outside.name)
    od = OutDir(out)
    assert DATE not in od.dates()
    with pytest.raises(KeyError) as caught:
        od.load(DATE)
    assert marker not in str(caught.value)


def test_inside_session_symlink_is_not_followed(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    marker = "INSIDE-LINK-TARGET"
    (out / "decoy.json").write_text('{"marker":"' + marker + '"}')
    (out / f"session-{DATE}.json").symlink_to("decoy.json")
    od = OutDir(out)
    assert od.dates() == []
    with pytest.raises(KeyError) as caught:
        od.load(DATE)
    assert marker not in str(caught.value)


@pytest.mark.parametrize("suffix", [
    ".v1_1.json", ".trace.json", ".trace.json.sha256", ".md", ".watch.json", ".watch.json.sha256",
])
def test_artifact_symlink_outside_root_rejects_load(tmp_path, suffix):
    out = tmp_path / "out"
    out.mkdir()
    (out / f"session-{DATE}.json").write_text('{"schema_version":"1.1.8"}')
    marker = "OUTSIDE-ARTIFACT-MARKER"
    outside = tmp_path / ("outside" + suffix)
    outside.write_text(marker)
    (out / f"session-{DATE}{suffix}").symlink_to(Path("..") / outside.name)
    od = OutDir(out)
    assert od.dates() == [DATE]
    with pytest.raises(KeyError) as caught:
        od.load(DATE)
    assert marker not in str(caught.value)


def test_fifo_sidecar_is_rejected_without_opening(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / f"session-{DATE}.json").write_text('{"schema_version":"1.1.8"}')
    os.mkfifo(out / f"session-{DATE}.watch.json.sha256")
    with pytest.raises(KeyError):
        OutDir(out).load(DATE)


def test_session_open_flags_are_nonblocking_and_nofollow():
    from optpipe.dashboard.data import _FILE_FLAGS
    assert _FILE_FLAGS & os.O_NONBLOCK
    assert _FILE_FLAGS & os.O_NOFOLLOW
    assert _FILE_FLAGS & os.O_CLOEXEC
    assert (_FILE_FLAGS & os.O_ACCMODE) == os.O_RDONLY


def test_single_link_regular_session_still_loads(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    body = '{"schema_version":"1.1.8","marker":"SINGLE-LINK-OK"}\n'
    path = out / f"session-{DATE}.json"
    path.write_text(body)
    assert path.stat().st_nlink == 1
    od = OutDir(out)
    assert od.dates() == [DATE]
    assert od.load(DATE).record["marker"] == "SINGLE-LINK-OK"
    assert od.read_contained(path.name) == body.encode()


def test_hardlink_of_outside_file_is_never_returned(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    marker = "OUTSIDE-HARDLINK-MARKER"
    outside = tmp_path / "outside.json"
    outside.write_text('{"marker":"' + marker + '"}')
    inside = out / f"session-{DATE}.json"
    os.link(outside, inside)
    assert inside.stat().st_nlink >= 2 and inside.stat().st_ino == outside.stat().st_ino
    good_day = "1999-01-05"
    (out / f"session-{good_day}.json").write_text('{"schema_version":"1.1.8","marker":"GOOD-SESSION"}')
    od = OutDir(out)
    assert od.dates() == [good_day]
    assert marker not in str(od.load(good_day).record)
    with pytest.raises(KeyError) as caught:
        od.load(DATE)
    assert marker not in str(caught.value)
    with pytest.raises(KeyError) as raw:
        od.read_contained(inside.name)
    assert marker not in str(raw.value)
    response = TestClient(create_app(out)).get(f"/files/{inside.name}")
    assert response.status_code == 404
    assert marker not in response.text
    assert marker in outside.read_text()


def test_multi_link_names_sharing_an_inode_are_rejected(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    marker = "NLINK-MARKER"
    first = out / f"session-{DATE}.json"
    second = out / "session-1999-01-04.json"
    first.write_text('{"marker":"' + marker + '"}')
    os.link(first, second)
    assert first.stat().st_nlink == 2 and first.stat().st_ino == second.stat().st_ino
    od = OutDir(out)
    assert od.dates() == []
    for day in (DATE, "1999-01-04"):
        with pytest.raises(KeyError) as caught:
            od.load(day)
        assert marker not in str(caught.value)
    client = TestClient(create_app(out))
    for name in (first.name, second.name):
        response = client.get("/files/" + name)
        assert response.status_code == 404
        assert marker not in response.text


def test_request_string_is_not_the_stat_or_open_path(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    body = b'{"schema_version":"1.1.8"}\n'
    (out / f"session-{DATE}.json").write_bytes(body)
    requested = f"session-{DATE}.json"
    stat_paths, open_paths = [], []
    real_stat, real_open = os.stat, os.open

    def spy_stat(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            stat_paths.append(path)
        return real_stat(path, *args, **kwargs)

    def spy_open(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            open_paths.append((path, flags))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "stat", spy_stat)
    monkeypatch.setattr(os, "open", spy_open)
    assert OutDir(out).read_contained(requested) == body
    assert stat_paths and open_paths
    assert all(path == requested and path is not requested and "/" not in path for path in stat_paths)
    assert all(path == requested and path is not requested and "/" not in path and flags & os.O_NONBLOCK
               for path, flags in open_paths)
    traversal = "../" + requested
    stat_paths.clear()
    open_paths.clear()
    with pytest.raises(KeyError) as caught:
        OutDir(out).read_contained(traversal)
    assert "OUTSIDE" not in str(caught.value)
    assert all(path is not traversal for path in stat_paths)
    assert all(path is not traversal for path, _flags in open_paths)


def test_second_link_between_stat_and_open_is_rejected(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    marker = "POST-OPEN-NLINK-MARKER"
    session = out / f"session-{DATE}.json"
    session.write_text('{"schema_version":"1.1.8","marker":"' + marker + '"}')
    extra = out / "session-1999-01-04.json"
    real_open = os.open
    linked = {"done": False}

    def linking_open(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None and not linked["done"] and path == session.name:
            linked["done"] = True
            os.link(session, extra)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", linking_open)
    with pytest.raises(KeyError) as caught:
        OutDir(out).load(DATE)
    assert marker not in str(caught.value)
    assert linked["done"] and session.stat().st_nlink == 2


def test_fifo_swapped_in_before_open_does_not_block(tmp_path, monkeypatch):
    """Replace a regular file with a FIFO after lstat and before open.

    This is the swap this process can make deterministically. An attacker in another
    process is not scheduled here; O_NONBLOCK is what keeps that open from parking,
    and the post-open fstat rejects the non-regular descriptor.
    """
    out = tmp_path / "out"
    out.mkdir()
    marker = "FIFO-RACE-MARKER"
    session = out / f"session-{DATE}.json"
    session.write_text('{"marker":"' + marker + '"}')
    real_open = os.open
    swapped = {"done": False}
    file_flags = []

    def swapping_open(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None and path == session.name:
            file_flags.append(flags)
            if not swapped["done"]:
                swapped["done"] = True
                os.unlink(session)
                os.mkfifo(session)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    def _alarm(signum, frame):
        raise TimeoutError("session open blocked on a FIFO")

    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(2)
    try:
        with pytest.raises(KeyError) as caught:
            OutDir(out).load(DATE)
        assert marker not in str(caught.value)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert swapped["done"] and file_flags
    assert all(flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW for flags in file_flags)


def test_session_page_rejects_watch_sidecar_symlink(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / f"session-{DATE}.json").write_text('{"schema_version":"1.1.8"}')
    marker = "OUTSIDE-SIDECAR-MARKER"
    outside = tmp_path / "outside.sha256"
    outside.write_text(marker)
    (out / f"session-{DATE}.watch.json.sha256").symlink_to(Path("..") / outside.name)
    response = TestClient(create_app(out)).get(f"/s/{DATE}/today")
    assert response.status_code == 404
    assert marker not in response.text


@pytest.mark.parametrize("name", [
    "session-1999-01-01.json", "session-1999-01-01.md", "session-1999-01-01.trace.json.sha256",
])
def test_raw_symlink_outside_root_is_not_served(tmp_path, name):
    out = tmp_path / "out"
    out.mkdir()
    (out / f"session-{DATE}.json").write_text('{"schema_version":"1.1.8"}')
    marker = "RAW-OUTSIDE-MARKER"
    outside = tmp_path / "leak-body"
    outside.write_text(marker)
    (out / name).symlink_to(outside)
    client = TestClient(create_app(out))
    response = client.get(f"/files/{name}")
    assert response.status_code == 404
    assert marker not in response.text
    assert client.get(f"/files/session-{DATE}.json").status_code == 200


def test_history_stays_up_when_sidecar_symlink_escapes(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / f"session-{DATE}.json").write_text('{"schema_version":"1.1.8","risk_reviews":[],"idea_log":[]}')
    outside = tmp_path / "outside.sha256"
    outside.write_text("OUTSIDE-SIDECAR-MARKER")
    (out / f"session-{DATE}.watch.json.sha256").symlink_to(Path("..") / outside.name)
    response = TestClient(create_app(out)).get("/history")
    assert response.status_code == 200
    assert "OUTSIDE-SIDECAR-MARKER" not in response.text
    assert "unreadable" in response.text


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
