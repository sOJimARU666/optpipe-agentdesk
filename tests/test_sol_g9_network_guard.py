"""SOL-ACCEPT-001 G9 (R6): the runtime network guard installed by tests/conftest.py.

Every network path the code could take fails during tests, in this process and in every Python subprocess
(the optpipe CLI and scripts/*.py). A non-GET is named as such, and a GET is still blocked. The in-process
TestClient and other in-memory transports are allowed. Opt-in `live` tests are exempt.

Every direct call below targets loopback port 9 (discard) with a short timeout. Without a guard, the call is
refused locally, so even the baseline run sends nothing off the box. A test that would resolve a real name first
asserts that the guard wraps the function, so the baseline never makes that call either. PAPER ONLY."""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "2026-10-13"
LOOP = ("127.0.0.1", 9)
URL = "http://127.0.0.1:9/paper-only-probe"
blocked_ok = pytest.mark.netguard_expected


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    """Loopback must mean loopback: no HTTP(S)/ALL proxy from the environment may reroute a probe."""
    for k in list(os.environ):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


def _assert_guard(exc_info, *words):
    e = exc_info.value
    assert "network guard" in str(e), f"not stopped by the network guard: {type(e).__name__}: {e}"
    assert not isinstance(e, Exception), "guard error must not be an Exception (an `except Exception` would hide it)"
    for w in words:
        assert w in str(e), f"{w!r} not in {e}"


def _wrapped(fn) -> bool:
    return hasattr(fn, "__wrapped__")


# ---------------------------------------------------------------- installed (Moodle probe test_R6_runtime_guard_installed)
def test_runtime_guard_installed():
    import http.client
    import httpx
    import requests
    for fn in (socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto, socket.create_connection,
               socket.getaddrinfo, requests.Session.request, requests.Session.send, urllib.request.urlopen,
               urllib.request.OpenerDirector.open, http.client.HTTPConnection.connect, http.client.HTTPConnection.request,
               httpx.Client.send, httpx.AsyncClient.send, asyncio.open_connection):
        assert _wrapped(fn), f"{fn.__qualname__} is not guarded"


# ---------------------------------------------------------------- socket layer
@blocked_ok
def test_runtime_guard_blocks_socket_connect():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        with pytest.raises(BaseException) as ei:
            s.connect(LOOP)
        _assert_guard(ei, "socket.connect")
    finally:
        s.close()


@blocked_ok
def test_runtime_guard_blocks_socket_connect_ex():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        with pytest.raises(BaseException) as ei:
            s.connect_ex(LOOP)
        _assert_guard(ei)
    finally:
        s.close()


@blocked_ok
def test_runtime_guard_blocks_udp_sendto():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(BaseException) as ei:
            s.sendto(b"x", LOOP)
        _assert_guard(ei, "socket.sendto")
    finally:
        s.close()


@blocked_ok
def test_runtime_guard_blocks_create_connection():
    with pytest.raises(BaseException) as ei:
        socket.create_connection(LOOP, timeout=1)
    _assert_guard(ei, "socket.create_connection")


@blocked_ok
def test_runtime_guard_blocks_dns_lookup():
    assert _wrapped(socket.getaddrinfo), "socket.getaddrinfo is not guarded (not calling it unguarded)"
    with pytest.raises(BaseException) as ei:
        socket.getaddrinfo("paper-only-probe.invalid", 443)
    _assert_guard(ei, "getaddrinfo")


# ---------------------------------------------------------------- requests
@blocked_ok
def test_runtime_guard_blocks_requests_session_post():
    import requests
    with pytest.raises(BaseException) as ei:
        requests.Session().post(URL, data=b"x", timeout=1)
    _assert_guard(ei, "non-GET POST")


@blocked_ok
@pytest.mark.parametrize("method", ["put", "patch", "delete", "head", "options"])
def test_runtime_guard_blocks_requests_other_non_get(method):
    import requests
    with pytest.raises(BaseException) as ei:
        getattr(requests, method)(URL, timeout=1)
    _assert_guard(ei, f"non-GET {method.upper()}")


@blocked_ok
def test_runtime_guard_blocks_requests_get_too():
    import requests
    with pytest.raises(BaseException) as ei:
        requests.get(URL, timeout=1)
    _assert_guard(ei, "GET")
    assert "non-GET" not in str(ei.value)


@blocked_ok
def test_runtime_guard_blocks_requests_prepared_send():
    import requests
    s = requests.Session()
    with pytest.raises(BaseException) as ei:
        s.send(requests.Request("POST", URL, data=b"x").prepare(), timeout=1)
    _assert_guard(ei, "requests.Session.send")


@blocked_ok
def test_runtime_guard_blocks_getattr_built_method():
    import requests
    with pytest.raises(BaseException) as ei:
        getattr(requests, "po" + "st")(URL, data=b"x", timeout=1)
    _assert_guard(ei, "non-GET POST")


# ---------------------------------------------------------------- urllib / http.client
@blocked_ok
def test_runtime_guard_blocks_urllib_post():
    req = urllib.request.Request(URL, data=b"x", method="POST")
    with pytest.raises(BaseException) as ei:
        urllib.request.urlopen(req, timeout=1)
    _assert_guard(ei, "urlopen")


@blocked_ok
def test_runtime_guard_blocks_urllib_opener():
    with pytest.raises(BaseException) as ei:
        urllib.request.build_opener().open(URL, timeout=1)
    _assert_guard(ei, "OpenerDirector.open")


@blocked_ok
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_runtime_guard_blocks_http_client(method):
    import http.client
    c = http.client.HTTPConnection(*LOOP, timeout=1)
    try:
        with pytest.raises(BaseException) as ei:
            c.request(method, "/")
        _assert_guard(ei, "http.client")
    finally:
        c.close()


# ---------------------------------------------------------------- httpx / asyncio
@blocked_ok
@pytest.mark.parametrize("method", ["get", "post"])
def test_runtime_guard_blocks_httpx(method):
    import httpx
    with httpx.Client(timeout=1) as c:
        with pytest.raises(BaseException) as ei:
            getattr(c, method)(URL)
    _assert_guard(ei, "httpx")


@blocked_ok
def test_runtime_guard_blocks_httpx_async():
    import httpx

    async def go():
        async with httpx.AsyncClient(timeout=1) as c:
            await c.post(URL)
    with pytest.raises(BaseException) as ei:
        asyncio.run(go())
    _assert_guard(ei, "httpx")


@blocked_ok
def test_runtime_guard_blocks_asyncio_open_connection():
    async def go():
        await asyncio.wait_for(asyncio.open_connection(*LOOP), 1)
    with pytest.raises(BaseException) as ei:
        asyncio.run(go())
    _assert_guard(ei, "asyncio.open_connection")


@blocked_ok
def test_runtime_guard_blocks_asyncio_loop_create_connection():
    async def go():
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.create_connection(asyncio.Protocol, *LOOP), 1)
    with pytest.raises(BaseException) as ei:
        asyncio.run(go())
    _assert_guard(ei, "create_connection")


# ---------------------------------------------------------------- the one real network caller cannot swallow it
@blocked_ok
def test_http_fetcher_cannot_swallow_the_guard():
    from optpipe.providers.fetchers import HttpFetcher
    with pytest.raises(BaseException) as ei:
        HttpFetcher("paper-only-test", timeout_s=1).get("nasdaq_info", "JPM", URL)
    _assert_guard(ei, "GET")


# ---------------------------------------------------------------- subprocesses: the CLI and scripts/
@blocked_ok
def test_subprocess_python_is_guarded():
    code = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9/', timeout=1)"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "network guard" in r.stderr, r.stderr[-600:]


@blocked_ok
def test_scripts_run_under_the_guard(tmp_path):
    """A scripts/*.py run as a subprocess (the way CI and the tests run them) gets the guard installed."""
    probe = tmp_path / "probe.py"
    probe.write_text("import runpy, sys, socket\n"
                     "assert hasattr(socket.socket.connect, '__wrapped__'), 'guard missing in subprocess'\n"
                     "sys.argv = [sys.argv[1], '--help']\n"
                     "try:\n    runpy.run_path(sys.argv[0], run_name='__main__')\nexcept SystemExit:\n    pass\n"
                     "socket.create_connection(('127.0.0.1', 9), timeout=1)\n")
    for script in sorted((ROOT / "scripts").glob("*.py")):
        r = subprocess.run([sys.executable, str(probe), str(script)], capture_output=True, text=True, timeout=180,
                           cwd=ROOT)
        assert r.returncode != 0 and "network guard" in r.stderr, f"{script.name}: {r.stderr[-600:]}"


def test_cli_subprocess_run_is_offline_under_guard(tmp_path):
    """`optpipe run` from the fixtures, as a subprocess with the guard on, makes no network call (the hook would fail
    this test if it did) and exits 0."""
    r = subprocess.run([sys.executable, "-c", "import socket; print(hasattr(socket.socket.connect, '__wrapped__'))"],
                       capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "True", "guard not installed in subprocesses"
    exe = Path(sys.executable).parent / "optpipe"
    r = subprocess.run([str(exe), "run", "--fixtures", str(FIXTURES), "--out", str(tmp_path)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-800:]
    assert (tmp_path / "session-2026-10-13.json").is_file()


def test_full_offline_run_makes_no_network_call(tmp_path):
    """The pipeline and the watch replay, in-process, with the real (unmocked) network stack under the guard.
    The conftest hook fails this test if any blocked call was made, even one the code caught."""
    assert _wrapped(socket.socket.connect), "no runtime guard active"
    from optpipe.cli import main
    assert main(["run", "--fixtures", str(FIXTURES), "--out", str(tmp_path)]) == 0
    sess = tmp_path / "session-2026-10-13.json"
    assert main(["watch", "--session", str(sess), "--replay", str(FIXTURES / "watch_tape.yaml"),
                 "--out", str(tmp_path)]) == 0
    assert (tmp_path / "session-2026-10-13.watch.json").is_file()


# ---------------------------------------------------------------- allowed: in-process transports and AF_UNIX
def test_testclient_in_process_is_allowed(tmp_path):
    from fastapi.testclient import TestClient
    from optpipe.dashboard.app import create_app
    assert TestClient(create_app(tmp_path)).get("/").status_code == 200


def test_httpx_mock_transport_is_allowed():
    import httpx
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))) as c:
        assert c.get("http://paper-only-probe.invalid/").text == "ok"


def test_af_unix_socketpair_is_allowed():
    a, b = socket.socketpair()
    try:
        a.sendall(b"x")
        assert b.recv(1) == b"x"
    finally:
        a.close(), b.close()


# ---------------------------------------------------------------- the hook: swallowed calls fail; live is exempt
def test_hook_fails_swallowed_calls_and_exempts_live(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "OPTPIPE_NETGUARD_LOG"}
    env["OPTPIPE_LIVE_TESTS"] = "1"
    junit = tmp_path / "j.xml"
    r = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", f"--junitxml={junit}",
                        str(ROOT / "tests" / "netguard" / "selftest_netguard_hook.py")],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)
    assert junit.is_file(), r.stdout[-800:] + r.stderr[-800:]
    import xml.etree.ElementTree as ET
    got = {}
    for tc in ET.parse(junit).getroot().iter("testcase"):
        bad = [c for c in tc if c.tag in ("failure", "error")]
        got[tc.get("name")] = ("fail: " + (bad[0].get("message") or "")) if bad else \
            ("skip" if any(c.tag == "skipped" for c in tc) else "pass")
    assert got["test_swallowed_in_process_violation_still_fails"].startswith("fail: AssertionError: runtime network guard")
    assert got["test_swallowed_subprocess_violation_still_fails"].startswith("fail: AssertionError: runtime network guard")
    assert got["test_live_test_is_exempt"] == "pass", got
    assert got["test_clean_test_passes"] == "pass", got
